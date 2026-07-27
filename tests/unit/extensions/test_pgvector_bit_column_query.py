"""Contracts for pgvector bit-column catalog discovery."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from postgres_mcp.pgvector_diagnostics import PostgresPgvectorRepository
from postgres_mcp.sql import BoundedQueryResult


def result(rows: list[dict[str, object]]) -> BoundedQueryResult:
    return BoundedQueryResult(rows, [], len(rows), False, None, "SELECT")


@pytest.mark.asyncio
async def test_bit_columns_require_a_pgvector_owned_index_key() -> None:
    driver = AsyncMock()
    driver.execute_bounded_query.side_effect = [
        result([{"extension_oid": 8100, "installed_version": "0.8.2", "schema_name": "public"}]),
        result([]),
        result([]),
    ]

    await PostgresPgvectorRepository(driver).snapshot(max_columns=25, max_indexes=25)

    column_call = driver.execute_bounded_query.await_args_list[1]
    normalized_sql = " ".join(column_call.args[0].split()).lower()
    assert "extension_index_keys" in normalized_sql
    assert "type.typname = 'bit'" in normalized_sql
    assert "key.indrelid = attribute.attrelid" in normalized_sql
    assert "key.attribute_number = attribute.attnum" in normalized_sql
    assert column_call.kwargs["params"] == [8100]
    assert column_call.kwargs["force_readonly"] is True
