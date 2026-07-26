"""Real-PostgreSQL contracts for generic extension capability profiles."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from postgres_mcp.extension_profiles import ExtensionFamily
from postgres_mcp.extension_profiles import PostgresExtensionProfileRepository
from postgres_mcp.sql import SqlDriver


@pytest_asyncio.fixture
async def extension_driver(test_postgres_connection_string: tuple[str, str]) -> AsyncIterator[SqlDriver]:
    connection_string, _version = test_postgres_connection_string
    driver = SqlDriver(engine_url=connection_string)
    await driver.execute_query("CREATE EXTENSION IF NOT EXISTS hypopg", force_readonly=False)
    try:
        yield driver
    finally:
        await driver.execute_query("DROP EXTENSION IF EXISTS hypopg", force_readonly=False)
        connection = driver.connect()
        if hasattr(connection, "close"):
            await connection.close()


@pytest.mark.asyncio
async def test_installed_and_available_extensions_are_bounded_and_version_preserving(
    extension_driver: SqlDriver,
) -> None:
    snapshot = await PostgresExtensionProfileRepository(extension_driver).snapshot(include_available=True)

    by_name = {profile.name: profile for profile in snapshot.profiles}
    assert "plpgsql" in by_name
    assert by_name["plpgsql"].installed is True
    assert by_name["plpgsql"].family is ExtensionFamily.OTHER
    assert by_name["plpgsql"].installed_version
    assert "hypopg" in by_name
    assert by_name["hypopg"].installed is True
    assert by_name["hypopg"].family is ExtensionFamily.HYPOPG
    assert by_name["hypopg"].specialized_tools == ("explain_query", "analyze_workload_indexes")
    assert snapshot.to_payload()["total_returned"] <= 500
