"""Supported-PostgreSQL contracts for reviewed nontransactional maintenance."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from postgres_mcp.maintenance import LEDGER_TABLE_NAME
from postgres_mcp.maintenance import MaintenanceOperation
from postgres_mcp.maintenance import MaintenanceOperationStatus
from postgres_mcp.maintenance import MaintenanceRequest
from postgres_mcp.maintenance import MaintenanceReviewMismatch
from postgres_mcp.maintenance import MaintenanceService
from postgres_mcp.maintenance import MaintenanceTarget
from postgres_mcp.maintenance import PostgresMaintenanceBackend
from postgres_mcp.maintenance import ReconciliationResolution
from postgres_mcp.sql import DbConnPool
from postgres_mcp.sql import SqlDriver

TEST_SCHEMA = "mcp_maintenance_test"


@pytest_asyncio.fixture
async def maintenance_context(
    test_postgres_connection_string: tuple[str, str],
) -> AsyncIterator[tuple[SqlDriver, MaintenanceService]]:
    connection_string, _version = test_postgres_connection_string
    driver = SqlDriver(engine_url=connection_string)
    await driver.execute_query(f"DROP SCHEMA IF EXISTS {TEST_SCHEMA} CASCADE", force_readonly=False)
    await driver.execute_query(f"CREATE SCHEMA {TEST_SCHEMA}", force_readonly=False)
    await driver.execute_query(
        f"""
        CREATE TABLE {TEST_SCHEMA}.items (
            id bigint PRIMARY KEY,
            category integer NOT NULL,
            payload text NOT NULL
        );
        INSERT INTO {TEST_SCHEMA}.items
        SELECT value, value % 2, repeat('x', 100)
        FROM generate_series(1, 100) AS value;
        CREATE INDEX items_category_idx ON {TEST_SCHEMA}.items (category);

        CREATE MATERIALIZED VIEW {TEST_SCHEMA}.item_summary AS
        SELECT category, count(*)::bigint AS item_count
        FROM {TEST_SCHEMA}.items
        GROUP BY category;
        CREATE UNIQUE INDEX item_summary_category_key
            ON {TEST_SCHEMA}.item_summary (category);
        """,
        force_readonly=False,
    )
    service = MaintenanceService(
        PostgresMaintenanceBackend(
            driver,
            ledger_schema=TEST_SCHEMA,
        )
    )
    try:
        yield driver, service
    finally:
        await driver.execute_query(f"DROP SCHEMA IF EXISTS {TEST_SCHEMA} CASCADE", force_readonly=False)
        if isinstance(driver.conn, DbConnPool):
            await driver.conn.close()


def request(
    name: str,
    operation: MaintenanceOperation,
    target_name: str,
) -> MaintenanceRequest:
    return MaintenanceRequest(
        name=name,
        operation=operation,
        target=MaintenanceTarget(TEST_SCHEMA, target_name),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("name", "operation", "target_name"),
    [
        ("vacuum-items", MaintenanceOperation.VACUUM_ANALYZE, "items"),
        ("analyze-items", MaintenanceOperation.ANALYZE, "items"),
        ("reindex-category", MaintenanceOperation.REINDEX_INDEX_CONCURRENTLY, "items_category_idx"),
        (
            "refresh-summary",
            MaintenanceOperation.REFRESH_MATERIALIZED_VIEW_CONCURRENTLY,
            "item_summary",
        ),
    ],
)
async def test_reviewed_maintenance_operations_succeed_and_are_idempotent(
    maintenance_context: tuple[SqlDriver, MaintenanceService],
    name: str,
    operation: MaintenanceOperation,
    target_name: str,
) -> None:
    _driver, service = maintenance_context
    reviewed = await service.plan(request(name, operation, target_name))

    applied = await service.apply(
        reviewed,
        review_hash=reviewed.review_hash,
        timeout_seconds=120,
        lock_timeout_seconds=10,
    )
    repeated = await service.apply(
        reviewed,
        review_hash=reviewed.review_hash,
        timeout_seconds=120,
        lock_timeout_seconds=10,
    )

    assert applied.status is MaintenanceOperationStatus.SUCCEEDED
    assert repeated.status is MaintenanceOperationStatus.ALREADY_SUCCEEDED


@pytest.mark.asyncio
async def test_target_oid_drift_is_rejected_before_execution(
    maintenance_context: tuple[SqlDriver, MaintenanceService],
) -> None:
    driver, service = maintenance_context
    reviewed = await service.plan(request("drifted-items", MaintenanceOperation.ANALYZE, "items"))
    await driver.execute_query(f"DROP TABLE {TEST_SCHEMA}.items CASCADE", force_readonly=False)
    await driver.execute_query(
        f"CREATE TABLE {TEST_SCHEMA}.items (id bigint PRIMARY KEY)",
        force_readonly=False,
    )

    with pytest.raises(MaintenanceReviewMismatch, match="review_hash"):
        current = await service.plan(request("drifted-items", MaintenanceOperation.ANALYZE, "items"))
        await service.apply(
            current,
            review_hash=reviewed.review_hash,
            timeout_seconds=60,
            lock_timeout_seconds=5,
        )


@pytest.mark.asyncio
async def test_status_is_redacted_and_unknown_outcome_requires_reconciliation(
    maintenance_context: tuple[SqlDriver, MaintenanceService],
) -> None:
    driver, service = maintenance_context
    reviewed = await service.plan(request("reconcile-items", MaintenanceOperation.ANALYZE, "items"))
    await service.apply(
        reviewed,
        review_hash=reviewed.review_hash,
        timeout_seconds=60,
        lock_timeout_seconds=5,
    )
    await driver.execute_query(
        f"""
        UPDATE {TEST_SCHEMA}.{LEDGER_TABLE_NAME}
        SET status = 'unknown', finished_at = NULL, error_code = 'test_unknown'
        WHERE name = %s
        """,
        params=[reviewed.name],
        force_readonly=False,
    )

    snapshot = await service.status(limit=10)
    rendered = str(snapshot.to_payload())
    assert snapshot.operations[0].status is MaintenanceOperationStatus.UNKNOWN
    assert "VACUUM" not in rendered
    assert "ANALYZE" not in rendered

    reconciled = await service.reconcile(
        name=reviewed.name,
        review_hash=reviewed.review_hash,
        resolution=ReconciliationResolution.FAILED,
    )
    assert reconciled.status is MaintenanceOperationStatus.RECONCILED_FAILED
