"""Blocking PostgreSQL pool, concurrency, and cancellation stress contracts."""

from __future__ import annotations

import asyncio
import os

import pytest
from psycopg.pq import TransactionStatus
from psycopg.rows import dict_row
from psycopg_pool import PoolTimeout

from postgres_mcp.sql import DbConnPool
from postgres_mcp.sql import SqlDriver

pytestmark = pytest.mark.skipif(
    os.environ.get("PGSQL_MCP_RUN_STRESS") != "1",
    reason="set PGSQL_MCP_RUN_STRESS=1 to run the dedicated stress contracts",
)


async def _close_pool(pool: DbConnPool) -> None:
    await pool.close()


@pytest.mark.asyncio
async def test_exhausted_pool_times_out_then_recovers(
    test_postgres_connection_string: tuple[str, str],
) -> None:
    connection_string, _version = test_postgres_connection_string
    pool = DbConnPool(connection_string, min_size=0, max_size=1)
    backend = await pool.pool_connect()

    try:
        async with backend.connection():
            with pytest.raises(PoolTimeout):
                async with backend.connection(timeout=0.1):
                    pytest.fail("an exhausted one-connection pool unexpectedly granted a second lease")

        async with backend.connection(timeout=2) as connection:
            async with connection.cursor(row_factory=dict_row) as cursor:
                await cursor.execute("SELECT 1 AS value")
                row = await cursor.fetchone()
                assert row == {"value": 1}
    finally:
        await _close_pool(pool)


@pytest.mark.asyncio
async def test_one_hundred_concurrent_bounded_reads_leave_no_idle_transaction(
    test_postgres_connection_string: tuple[str, str],
) -> None:
    connection_string, _version = test_postgres_connection_string
    pool = DbConnPool(connection_string, min_size=0, max_size=5)
    driver = SqlDriver(conn=pool)

    async def read_value(value: int) -> int:
        result = await driver.execute_bounded_query(
            "SELECT %s::integer AS value",
            params=[value],
            max_rows=1,
            force_readonly=True,
            timeout_seconds=5,
        )
        assert result.rows == [{"value": value}]
        assert result.truncated is False
        return int(result.rows[0]["value"])

    try:
        values = await asyncio.wait_for(
            asyncio.gather(*(read_value(value) for value in range(100))),
            timeout=45,
        )
        assert values == list(range(100))

        async with driver.connection() as connection:
            assert connection.info.transaction_status is TransactionStatus.IDLE
            async with connection.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(
                    """
                    SELECT count(*)::integer AS leaked
                    FROM pg_catalog.pg_stat_activity
                    WHERE datname = pg_catalog.current_database()
                      AND usename = CURRENT_USER
                      AND state = 'idle in transaction'
                    """
                )
                row = await cursor.fetchone()
                assert row == {"leaked": 0}
            await connection.rollback()
    finally:
        await _close_pool(pool)


@pytest.mark.asyncio
async def test_cancelled_bounded_read_rolls_back_closes_cursor_and_reuses_connection(
    test_postgres_connection_string: tuple[str, str],
) -> None:
    connection_string, _version = test_postgres_connection_string
    pool = DbConnPool(connection_string, min_size=0, max_size=1)
    driver = SqlDriver(conn=pool)

    try:
        async with driver.connection() as connection:
            operation = asyncio.create_task(
                driver._execute_bounded_with_connection(  # pyright: ignore[reportPrivateUsage]
                    connection,
                    "SELECT pg_catalog.pg_sleep(10), 1 AS value",
                    params=None,
                    max_rows=1,
                    force_readonly=True,
                    timeout_seconds=20,
                )
            )
            await asyncio.sleep(0.2)
            operation.cancel()

            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(operation, timeout=5)

            assert connection.info.transaction_status is TransactionStatus.IDLE
            async with connection.cursor(row_factory=dict_row) as cursor:
                await cursor.execute("SELECT count(*)::integer AS cursor_count FROM pg_catalog.pg_cursors WHERE name LIKE 'pgsql_mcp_%'")
                cursor_row = await cursor.fetchone()
                assert cursor_row == {"cursor_count": 0}
                await cursor.execute("SELECT 1 AS reusable")
                reusable_row = await cursor.fetchone()
                assert reusable_row == {"reusable": 1}
            await connection.rollback()
    finally:
        await _close_pool(pool)
