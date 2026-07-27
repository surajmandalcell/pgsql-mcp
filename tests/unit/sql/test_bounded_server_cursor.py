"""Test-first contracts for memory-bounded public read queries."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from typing import Any
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import call

import pytest
from psycopg.rows import dict_row

from postgres_mcp.sql.results import ColumnInfo
from postgres_mcp.sql.sql_driver import SqlDriver


class TrackingAsyncContext:
    """Async context manager that records deterministic cursor cleanup."""

    def __init__(self, value: Any):
        self.value = value
        self.exited = False
        self.exit_error: BaseException | None = None

    async def __aenter__(self) -> Any:
        return self.value

    async def __aexit__(self, exc_type: Any, exc: BaseException | None, traceback: Any) -> None:
        self.exited = True
        self.exit_error = exc


class Description:
    name = "value"
    type_code = 23
    internal_size = 4
    precision = None
    scale = None
    null_ok = True


def make_cursor(
    *,
    rows: Sequence[dict[str, Any]] = (),
    description: Sequence[Any] | None = None,
    rowcount: int = -1,
    execute_error: BaseException | None = None,
) -> MagicMock:
    cursor = MagicMock()
    cursor.description = description
    cursor.rowcount = rowcount
    cursor.execute = AsyncMock(side_effect=execute_error)
    cursor.fetchmany = AsyncMock(return_value=list(rows))
    return cursor


def make_connection(*cursors: MagicMock) -> tuple[MagicMock, list[TrackingAsyncContext]]:
    contexts = [TrackingAsyncContext(cursor) for cursor in cursors]
    connection = MagicMock()
    connection.cursor = MagicMock(side_effect=contexts)
    connection.commit = AsyncMock()
    connection.rollback = AsyncMock()
    return connection, contexts


@pytest.mark.asyncio
async def test_read_only_bounded_query_uses_named_server_cursor_and_strict_fetch_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    control = make_cursor()
    reader = make_cursor(
        rows=[{"value": 1}, {"value": 2}],
        description=[Description()],
    )
    connection, contexts = make_connection(control, reader)
    monkeypatch.setattr("postgres_mcp.sql.sql_driver.secrets.token_hex", lambda length: "a1b2c3d4")
    driver = SqlDriver(conn=connection)

    result = await driver.execute_bounded_query(
        "SELECT %s::integer AS value",
        params=[1],
        max_rows=1,
        force_readonly=True,
        timeout_seconds=2.5,
    )

    assert result.rows == [{"value": 1}]
    assert result.columns == [ColumnInfo("value", 23, 4, None, None, True)]
    assert result.truncated is True
    assert connection.cursor.call_args_list == [
        call(row_factory=dict_row),
        call(name="pgsql_mcp_a1b2c3d4", row_factory=dict_row),
    ]
    control.execute.assert_any_await("BEGIN TRANSACTION READ ONLY")
    control.execute.assert_any_await(
        "SELECT set_config('statement_timeout', %s, true)",
        ["2500ms"],
    )
    reader.execute.assert_awaited_once_with("SELECT %s::integer AS value", [1])
    reader.fetchmany.assert_awaited_once_with(2)
    assert contexts[0].exited is True
    assert contexts[1].exited is True
    connection.rollback.assert_awaited_once()
    connection.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_named_server_cursor_is_closed_before_cancellation_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    control = make_cursor()
    reader = make_cursor(execute_error=asyncio.CancelledError())
    connection, contexts = make_connection(control, reader)
    monkeypatch.setattr("postgres_mcp.sql.sql_driver.secrets.token_hex", lambda length: "cancelled")
    driver = SqlDriver(conn=connection)

    with pytest.raises(asyncio.CancelledError):
        await driver._execute_bounded_with_connection(  # pyright: ignore[reportPrivateUsage]
            connection,
            "SELECT generate_series(1, 1000000)",
            params=None,
            max_rows=10,
            force_readonly=True,
            timeout_seconds=1,
        )

    assert contexts[1].exited is True
    assert isinstance(contexts[1].exit_error, asyncio.CancelledError)
    connection.rollback.assert_awaited_once()
    connection.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("force_readonly", [True, False])
async def test_rollback_failure_is_logged_without_masking_query_failure(
    force_readonly: bool,
    caplog: pytest.LogCaptureFixture,
) -> None:
    failing_cursor = make_cursor(execute_error=RuntimeError("query failure"))
    if force_readonly:
        connection, _ = make_connection(make_cursor(), failing_cursor)
    else:
        connection, _ = make_connection(failing_cursor)
    connection.rollback = AsyncMock(side_effect=RuntimeError("rollback failure"))
    driver = SqlDriver(conn=connection)

    with caplog.at_level(logging.ERROR, logger="postgres_mcp.sql.sql_driver"):
        with pytest.raises(RuntimeError, match="query failure"):
            await driver._execute_bounded_with_connection(  # pyright: ignore[reportPrivateUsage]
                connection,
                "SELECT 1" if force_readonly else "UPDATE public.items SET active = true WHERE id = 1",
                params=None,
                max_rows=1,
                force_readonly=force_readonly,
                timeout_seconds=1,
            )

    assert "Error rolling back bounded query: rollback failure" in caplog.text


@pytest.mark.asyncio
async def test_non_readonly_bounded_statement_keeps_single_unnamed_cursor() -> None:
    cursor = make_cursor(description=None, rowcount=1)
    connection, contexts = make_connection(cursor)
    driver = SqlDriver(conn=connection)

    result = await driver.execute_bounded_query(
        "UPDATE public.items SET active = %s WHERE id = %s",
        params=[True, 1],
        max_rows=1,
        force_readonly=False,
        timeout_seconds=1,
    )

    assert result.affected_rows == 1
    assert connection.cursor.call_args_list == [call(row_factory=dict_row)]
    cursor.execute.assert_any_await(
        "UPDATE public.items SET active = %s WHERE id = %s",
        [True, 1],
    )
    assert contexts[0].exited is True
    connection.commit.assert_awaited_once()
    connection.rollback.assert_not_awaited()
