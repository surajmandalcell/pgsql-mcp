"""Unit tests for bounded catalog and dynamic PostgreSQL type introspection."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from postgres_mcp.catalog_advanced import MAX_CATALOG_ROWS
from postgres_mcp.catalog_advanced import get_postgres_type_data
from postgres_mcp.catalog_advanced import get_relation_details_data
from postgres_mcp.catalog_advanced import get_server_info_data
from postgres_mcp.catalog_advanced import list_postgres_types_data
from postgres_mcp.catalog_advanced import list_relations_data
from postgres_mcp.catalog_advanced import relation_kind_from_payload
from postgres_mcp.catalog_advanced import search_catalog_data
from postgres_mcp.sql import BoundedQueryResult
from postgres_mcp.sql import SqlDriver


def bounded(rows: list[dict[str, object]]) -> BoundedQueryResult:
    return BoundedQueryResult(
        rows=rows,
        columns=[],
        row_count=len(rows),
        truncated=False,
        affected_rows=None,
        command="SELECT",
    )


@pytest.mark.asyncio
async def test_get_server_info_combines_extensions() -> None:
    driver = AsyncMock(spec=SqlDriver)
    driver.execute_bounded_query.side_effect = [
        bounded([{"database": "app", "server_version_num": 160000}]),
        bounded([{"name": "hstore", "version": "1.8"}]),
    ]

    payload = await get_server_info_data(driver)

    assert payload["database"] == "app"
    assert payload["extensions"] == [{"name": "hstore", "version": "1.8"}]
    assert driver.execute_bounded_query.await_count == 2
    assert all(call.kwargs["force_readonly"] is True for call in driver.execute_bounded_query.await_args_list)

    server_query = driver.execute_bounded_query.await_args_list[0].args[0]
    assert "pg_catalog.pg_database AS d" in server_query
    assert "d.datcollate AS lc_collate" in server_query
    assert "d.datctype AS lc_ctype" in server_query
    assert "current_setting('lc_collate')" not in server_query
    assert "current_setting('lc_ctype')" not in server_query


@pytest.mark.asyncio
async def test_search_catalog_validates_and_binds_filters() -> None:
    driver = AsyncMock(spec=SqlDriver)
    driver.execute_bounded_query.return_value = bounded([{"object_name": "orders", "object_kind": "table"}])

    rows = await search_catalog_data(
        driver,
        term="orders",
        schema_name="app",
        object_kind="table",
        limit=25,
        offset=5,
    )

    assert rows[0]["object_name"] == "orders"
    params = driver.execute_bounded_query.await_args.kwargs["params"]
    assert params == ["%orders%", "%orders%", "app", "app", "table", "table", False, 25, 5]
    assert driver.execute_bounded_query.await_args.kwargs["max_rows"] == 25

    with pytest.raises(ValueError, match="must not be empty"):
        await search_catalog_data(driver, term=" ")
    with pytest.raises(ValueError, match=f"cannot exceed {MAX_CATALOG_ROWS}"):
        await search_catalog_data(driver, term="x", limit=MAX_CATALOG_ROWS + 1)


@pytest.mark.asyncio
async def test_list_relations_maps_kind_without_dynamic_sql() -> None:
    driver = AsyncMock(spec=SqlDriver)
    driver.execute_bounded_query.return_value = bounded([{"relation_kind": "partitioned_table"}])

    rows = await list_relations_data(driver, schema_name="app", relation_kind="partitioned_table", limit=10)

    assert rows == [{"relation_kind": "partitioned_table"}]
    params = driver.execute_bounded_query.await_args.kwargs["params"]
    assert params[2:4] == ["p", "p"]
    with pytest.raises(ValueError, match="unsupported relation_kind"):
        await list_relations_data(driver, relation_kind="heapish")


@pytest.mark.asyncio
async def test_list_types_supports_every_catalog_kind() -> None:
    driver = AsyncMock(spec=SqlDriver)
    driver.execute_bounded_query.return_value = bounded([{"type_kind": "multirange", "oid": 90001}])

    rows = await list_postgres_types_data(driver, schema_name="app", type_kind="multirange")

    assert rows[0]["type_kind"] == "multirange"
    params = driver.execute_bounded_query.await_args.kwargs["params"]
    assert params[:4] == ["app", "app", "multirange", "multirange"]
    with pytest.raises(ValueError, match="unsupported type_kind"):
        await list_postgres_types_data(driver, type_kind="scalarish")


@pytest.mark.asyncio
async def test_get_enum_type_includes_ordered_labels() -> None:
    driver = AsyncMock(spec=SqlDriver)
    driver.execute_bounded_query.side_effect = [
        bounded([{"oid": 1001, "type_kind": "enum", "type_name": "mood"}]),
        bounded([{"label": "sad", "sort_order": 1.0}, {"label": "ok", "sort_order": 2.0}]),
    ]

    payload = await get_postgres_type_data(driver, schema_name="app", type_name="mood")

    assert payload["enum_labels"] == [{"label": "sad", "sort_order": 1.0}, {"label": "ok", "sort_order": 2.0}]
    assert payload["domain_constraints"] == []
    assert payload["composite_attributes"] == []
    assert payload["range"] is None


@pytest.mark.asyncio
async def test_get_domain_and_composite_and_range_details() -> None:
    domain_driver = AsyncMock(spec=SqlDriver)
    domain_driver.execute_bounded_query.side_effect = [
        bounded([{"oid": 2001, "type_kind": "domain"}]),
        bounded([{"constraint_name": "positive", "definition": "CHECK (VALUE > 0)"}]),
    ]
    domain = await get_postgres_type_data(domain_driver, type_oid=2001)
    assert domain["domain_constraints"][0]["constraint_name"] == "positive"

    composite_driver = AsyncMock(spec=SqlDriver)
    composite_driver.execute_bounded_query.side_effect = [
        bounded([{"oid": 3001, "type_kind": "composite", "relation_oid": 4001}]),
        bounded([{"attribute_name": "street", "type_oid": 25}]),
    ]
    composite = await get_postgres_type_data(composite_driver, type_oid=3001)
    assert composite["composite_attributes"] == [{"attribute_name": "street", "type_oid": 25}]

    range_driver = AsyncMock(spec=SqlDriver)
    range_driver.execute_bounded_query.side_effect = [
        bounded([{"oid": 5001, "type_kind": "multirange"}]),
        bounded([{"range_type_oid": 5000, "multirange_type_oid": 5001, "subtype_name": "numeric"}]),
    ]
    range_payload = await get_postgres_type_data(range_driver, type_oid=5001)
    assert range_payload["range"]["range_type_oid"] == 5000


@pytest.mark.asyncio
async def test_get_type_rejects_ambiguous_or_missing_identity() -> None:
    driver = AsyncMock(spec=SqlDriver)
    with pytest.raises(ValueError, match="provide type_oid"):
        await get_postgres_type_data(driver, schema_name="app")
    with pytest.raises(ValueError, match="greater than zero"):
        await get_postgres_type_data(driver, type_oid=0)

    driver.execute_bounded_query.return_value = bounded([])
    with pytest.raises(ValueError, match="was not found"):
        await get_postgres_type_data(driver, type_oid=999999)


@pytest.mark.asyncio
async def test_relation_details_assembles_all_object_families() -> None:
    driver = AsyncMock(spec=SqlDriver)
    driver.execute_bounded_query.side_effect = [
        bounded([{"oid": 42, "relation_kind": "partitioned_table"}]),
        bounded([{"column_name": "id", "type_oid": 20}]),
        bounded([{"constraint_name": "items_pkey"}]),
        bounded([{"index_name": "items_pkey"}]),
        bounded([{"trigger_name": "audit_items"}]),
        bounded([{"policy_name": "tenant"}]),
        bounded([]),
        bounded([{"relation_name": "items_2026"}]),
        bounded([{"grantee": "app_reader", "privilege_type": "SELECT"}]),
    ]

    payload = await get_relation_details_data(driver, schema_name="app", relation_name="items")

    assert payload["columns"][0]["type_oid"] == 20
    assert payload["constraints"][0]["constraint_name"] == "items_pkey"
    assert payload["indexes"][0]["index_name"] == "items_pkey"
    assert payload["triggers"][0]["trigger_name"] == "audit_items"
    assert payload["policies"][0]["policy_name"] == "tenant"
    assert payload["children"][0]["relation_name"] == "items_2026"
    assert payload["privileges"][0]["grantee"] == "app_reader"
    assert driver.execute_bounded_query.await_count == 9

    index_query = driver.execute_bounded_query.await_args_list[3].args[0]
    assert "pg_get_expr(i.indpred, i.indrelid, false) AS predicate" in index_query


@pytest.mark.asyncio
async def test_relation_details_reports_missing_relation() -> None:
    driver = AsyncMock(spec=SqlDriver)
    driver.execute_bounded_query.return_value = bounded([])
    with pytest.raises(ValueError, match="was not found"):
        await get_relation_details_data(driver, schema_name="app", relation_name="missing")


def test_relation_kind_payload_guard() -> None:
    assert relation_kind_from_payload({"relation_kind": "table"}) == "table"
    assert relation_kind_from_payload({"relation_kind": "invalid"}) is None
    assert relation_kind_from_payload({}) is None
