"""Real-PostgreSQL contracts for conservative provider capability profiles."""

from __future__ import annotations

import pytest

from postgres_mcp.provider_profiles import DeploymentProvider
from postgres_mcp.provider_profiles import PostgresProviderProfileRepository
from postgres_mcp.sql import SqlDriver


@pytest.mark.asyncio
async def test_generic_postgres_remains_unknown_without_explicit_hint(
    test_postgres_connection_string: tuple[str, str],
) -> None:
    connection_string, expected_image = test_postgres_connection_string
    driver = SqlDriver(engine_url=connection_string)

    try:
        repository = PostgresProviderProfileRepository(driver, timeout_seconds=10)
        automatic = await repository.snapshot()
        explicit = await repository.snapshot(provider_hint="upstream")

        assert automatic.provider is DeploymentProvider.UNKNOWN
        assert automatic.evidence == ()
        assert automatic.runtime.server_version_num // 10000 == int(expected_image.split(":", 1)[1])
        assert automatic.runtime.max_wal_senders >= 0
        assert automatic.runtime.max_replication_slots >= 0
        assert explicit.provider is DeploymentProvider.UPSTREAM
        assert explicit.explicit_hint is DeploymentProvider.UPSTREAM

        payload_text = repr(automatic.to_payload()).lower()
        assert "password" not in payload_text
        assert connection_string.lower() not in payload_text
        assert "conninfo" not in payload_text
    finally:
        pool = driver.connect()
        if hasattr(pool, "close"):
            await pool.close()
