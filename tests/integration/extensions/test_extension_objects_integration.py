"""Real-PostgreSQL contracts for extension-owned object inventory."""

from __future__ import annotations

import pytest

from postgres_mcp.extension_objects import PostgresExtensionObjectRepository
from postgres_mcp.sql import SqlDriver


@pytest.mark.asyncio
async def test_plpgsql_inventory_uses_core_catalogs_and_is_deterministic(
    test_postgres_connection_string: tuple[str, str],
) -> None:
    connection_string, _version = test_postgres_connection_string
    driver = SqlDriver(engine_url=connection_string)

    try:
        repository = PostgresExtensionObjectRepository(driver, timeout_seconds=10)
        first = await repository.snapshot("plpgsql", limit=100)
        second = await repository.snapshot("plpgsql", limit=100)

        assert first == second
        assert first.extension.name == "plpgsql"
        assert first.extension.oid > 0
        assert first.extension.installed_version
        assert first.extension.schema == "pg_catalog"
        assert first.truncated is False
        assert first.objects
        assert any(item.object_type == "language" for item in first.objects)
        assert len({(item.catalog, item.object_oid, item.object_sub_id) for item in first.objects}) == len(first.objects)

        payload_text = repr(first.to_payload()).lower()
        assert "password" not in payload_text
        assert connection_string.lower() not in payload_text
        assert "conninfo" not in payload_text
    finally:
        pool = driver.connect()
        if hasattr(pool, "close"):
            await pool.close()
