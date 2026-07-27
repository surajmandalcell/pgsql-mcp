"""Real-PostgreSQL memory-bound contract for public read queries."""

from __future__ import annotations

import pytest
from psycopg.rows import dict_row

from postgres_mcp.sql import SqlDriver


@pytest.mark.asyncio
async def test_million_row_source_fetches_only_visible_ceiling_and_leaves_no_cursor(
    test_postgres_connection_string: tuple[str, str],
) -> None:
    connection_string, _version = test_postgres_connection_string
    driver = SqlDriver(engine_url=connection_string)

    async with driver.connection() as connection:
        result = await driver._execute_bounded_with_connection(  # pyright: ignore[reportPrivateUsage]
            connection,
            "SELECT value FROM generate_series(1, 1000000) AS values(value)",
            params=None,
            max_rows=10,
            force_readonly=True,
            timeout_seconds=15,
        )

        assert result.row_count == 10
        assert result.rows == [{"value": value} for value in range(1, 11)]
        assert result.truncated is True

        async with connection.cursor(row_factory=dict_row) as cursor:
            await cursor.execute("SELECT count(*) AS cursor_count FROM pg_catalog.pg_cursors WHERE name LIKE 'pgsql_mcp_%'")
            row = await cursor.fetchone()
            assert row is not None
            assert row["cursor_count"] == 0
        await connection.rollback()

    pool = driver.connect()
    if hasattr(pool, "close"):
        await pool.close()
