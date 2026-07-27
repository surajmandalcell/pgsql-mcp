"""Contracts for bounded writable statements that return rows."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock
from unittest.mock import MagicMock

import pytest
from psycopg.rows import dict_row

from postgres_mcp.sql.sql_driver import SqlDriver


class AsyncContext:
    def __init__(self, value: Any):
        self.value = value

    async def __aenter__(self) -> Any:
        return self.value

    async def __aexit__(self, exc_type: Any, exc: BaseException | None, traceback: Any) -> None:
        return None


class Description:
    name = "value"
    type_code = 23
    internal_size = 4
    precision = None
    scale = None
    null_ok = False


@pytest.mark.asyncio
async def test_writable_bounded_statement_truncates_returning_rows_and_commits() -> None:
    cursor = MagicMock()
    cursor.description = [Description()]
    cursor.rowcount = 2
    cursor.execute = AsyncMock()
    cursor.fetchmany = AsyncMock(return_value=[{"value": 10}, {"value": 20}])

    connection = MagicMock()
    connection.cursor.return_value = AsyncContext(cursor)
    connection.commit = AsyncMock()
    connection.rollback = AsyncMock()
    driver = SqlDriver(conn=connection)

    result = await driver._execute_bounded_with_connection(  # pyright: ignore[reportPrivateUsage]
        connection,
        "UPDATE public.items SET value = %s WHERE active = true RETURNING value",
        params=[10],
        max_rows=1,
        force_readonly=False,
        timeout_seconds=1,
    )

    assert result.rows == [{"value": 10}]
    assert result.row_count == 1
    assert result.truncated is True
    assert result.affected_rows == 2
    assert connection.cursor.call_args.kwargs == {"row_factory": dict_row}
    cursor.execute.assert_any_await(
        "UPDATE public.items SET value = %s WHERE active = true RETURNING value",
        [10],
    )
    cursor.fetchmany.assert_awaited_once_with(2)
    connection.commit.assert_awaited_once()
    connection.rollback.assert_not_awaited()
