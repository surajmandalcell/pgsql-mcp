"""PostgreSQL 15/16 integration contracts for typed guarded data operations."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from postgres_mcp.data_ops import ComparisonOperator
from postgres_mcp.data_ops import DataConflictError
from postgres_mcp.data_ops import DataService
from postgres_mcp.data_ops import DataValidationError
from postgres_mcp.data_ops import DeleteRowsRequest
from postgres_mcp.data_ops import FilterCondition
from postgres_mcp.data_ops import FilterSet
from postgres_mcp.data_ops import InsertRowsRequest
from postgres_mcp.data_ops import MutationGuard
from postgres_mcp.data_ops import OrderDirection
from postgres_mcp.data_ops import OrderTerm
from postgres_mcp.data_ops import PostgresDataRepository
from postgres_mcp.data_ops import QualifiedRelation
from postgres_mcp.data_ops import SelectRowsRequest
from postgres_mcp.data_ops import UpdateRowsRequest
from postgres_mcp.data_ops import UpsertRowsRequest
from postgres_mcp.sql import DbConnPool
from postgres_mcp.sql import SqlDriver

TEST_SCHEMA = "mcp_data_ops_test"


@pytest_asyncio.fixture
async def data_context(test_postgres_connection_string: tuple[str, str]) -> AsyncIterator[tuple[SqlDriver, DataService, QualifiedRelation]]:
    connection_string, _version = test_postgres_connection_string
    driver = SqlDriver(engine_url=connection_string)
    await driver.execute_query(f"DROP SCHEMA IF EXISTS {TEST_SCHEMA} CASCADE", force_readonly=False)
    await driver.execute_query(f"CREATE SCHEMA {TEST_SCHEMA}", force_readonly=False)
    await driver.execute_query(
        f"""
        CREATE TABLE {TEST_SCHEMA}.accounts (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            tenant_id integer NOT NULL,
            email text NOT NULL UNIQUE,
            name text NOT NULL,
            version integer NOT NULL DEFAULT 1,
            generated_label text GENERATED ALWAYS AS (tenant_id::text || ':' || name) STORED
        )
        """,
        force_readonly=False,
    )
    service = DataService(PostgresDataRepository(driver))
    try:
        yield driver, service, QualifiedRelation(TEST_SCHEMA, "accounts")
    finally:
        await driver.execute_query(f"DROP SCHEMA IF EXISTS {TEST_SCHEMA} CASCADE", force_readonly=False)
        if isinstance(driver.conn, DbConnPool):
            await driver.conn.close()


@pytest.mark.asyncio
async def test_insert_select_and_stable_keyset_pagination(data_context) -> None:
    _driver, service, target = data_context
    inserted = await service.insert(
        InsertRowsRequest(
            relation=target,
            rows=(
                {"tenant_id": 1, "email": "a@example.com", "name": "A"},
                {"tenant_id": 1, "email": "b@example.com", "name": "B"},
                {"tenant_id": 1, "email": "c@example.com", "name": "C"},
            ),
            returning=("id", "email"),
            guard=MutationGuard(max_affected_rows=3, expected_rows=3),
        )
    )
    assert inserted.affected_rows == 3

    first = await service.select(
        SelectRowsRequest(
            relation=target,
            columns=("id", "email"),
            filters=FilterSet(all_of=(FilterCondition("tenant_id", ComparisonOperator.EQ, 1),)),
            order_by=(OrderTerm("id", OrderDirection.ASC),),
            limit=2,
        )
    )
    second = await service.select(
        SelectRowsRequest(
            relation=target,
            columns=("id", "email"),
            filters=FilterSet(all_of=(FilterCondition("tenant_id", ComparisonOperator.EQ, 1),)),
            order_by=(OrderTerm("id", OrderDirection.ASC),),
            limit=2,
            cursor=first.next_cursor,
        )
    )

    assert [row["email"] for row in first.rows] == ["a@example.com", "b@example.com"]
    assert first.truncated is True and first.next_cursor
    assert [row["email"] for row in second.rows] == ["c@example.com"]
    assert second.truncated is False and second.next_cursor is None


@pytest.mark.asyncio
async def test_upsert_and_optimistic_update_are_atomic(data_context) -> None:
    _driver, service, target = data_context
    await service.insert(
        InsertRowsRequest(
            relation=target,
            rows=({"tenant_id": 1, "email": "a@example.com", "name": "A"},),
            returning=("id",),
            guard=MutationGuard(max_affected_rows=1, expected_rows=1),
        )
    )
    upserted = await service.upsert(
        UpsertRowsRequest(
            relation=target,
            rows=({"tenant_id": 1, "email": "a@example.com", "name": "A2", "version": 2},),
            conflict_columns=("email",),
            update_columns=("name", "version"),
            returning=("id", "name", "version"),
            guard=MutationGuard(max_affected_rows=1, expected_rows=1),
        )
    )
    account_id = upserted.rows[0]["id"]
    assert upserted.rows[0]["name"] == "A2"

    updated = await service.update(
        UpdateRowsRequest(
            relation=target,
            values={"name": "A3", "version": 3},
            filters=FilterSet(all_of=(FilterCondition("id", ComparisonOperator.EQ, account_id),)),
            concurrency=FilterSet(all_of=(FilterCondition("version", ComparisonOperator.EQ, 2),)),
            returning=("id", "name", "version"),
            guard=MutationGuard(max_affected_rows=1, expected_rows=1),
        )
    )
    assert updated.rows[0]["version"] == 3

    with pytest.raises(DataConflictError, match="expected 1"):
        await service.update(
            UpdateRowsRequest(
                relation=target,
                values={"name": "stale"},
                filters=FilterSet(all_of=(FilterCondition("id", ComparisonOperator.EQ, account_id),)),
                concurrency=FilterSet(all_of=(FilterCondition("version", ComparisonOperator.EQ, 2),)),
                returning=("id",),
                guard=MutationGuard(max_affected_rows=1, expected_rows=1),
            )
        )


@pytest.mark.asyncio
async def test_delete_ceiling_rolls_back_and_metadata_guards_generated_columns(data_context) -> None:
    _driver, service, target = data_context
    await service.insert(
        InsertRowsRequest(
            relation=target,
            rows=(
                {"tenant_id": 7, "email": "a@example.com", "name": "A"},
                {"tenant_id": 7, "email": "b@example.com", "name": "B"},
            ),
            returning=(),
            guard=MutationGuard(max_affected_rows=2, expected_rows=2),
        )
    )
    tenant_filter = FilterSet(all_of=(FilterCondition("tenant_id", ComparisonOperator.EQ, 7),))

    with pytest.raises(DataConflictError, match="maximum is 1"):
        await service.delete(
            DeleteRowsRequest(
                relation=target,
                filters=tenant_filter,
                concurrency=FilterSet(),
                returning=("id",),
                guard=MutationGuard(max_affected_rows=1),
            )
        )

    remaining = await service.select(
        SelectRowsRequest(
            relation=target,
            columns=("id",),
            filters=tenant_filter,
            order_by=(OrderTerm("id"),),
            limit=10,
        )
    )
    assert len(remaining.rows) == 2

    with pytest.raises(DataValidationError, match="generated"):
        await service.update(
            UpdateRowsRequest(
                relation=target,
                values={"generated_label": "forbidden"},
                filters=tenant_filter,
                concurrency=FilterSet(),
                returning=(),
                guard=MutationGuard(max_affected_rows=2),
            )
        )


@pytest.mark.asyncio
async def test_single_oversized_selected_row_is_rejected_without_mutation(data_context) -> None:
    _driver, service, target = data_context
    await service.insert(
        InsertRowsRequest(
            relation=target,
            rows=({"tenant_id": 9, "email": "large@example.com", "name": "x" * 600_000},),
            returning=("id",),
            guard=MutationGuard(max_affected_rows=1, expected_rows=1),
        )
    )

    with pytest.raises(DataValidationError, match="single selected row exceeds"):
        await service.select(
            SelectRowsRequest(
                relation=target,
                columns=("id", "name"),
                filters=FilterSet(
                    all_of=(FilterCondition("email", ComparisonOperator.EQ, "large@example.com"),)
                ),
                order_by=(OrderTerm("id"),),
                limit=1,
            )
        )
