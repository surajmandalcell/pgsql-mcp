"""Tests for bounded queries and guarded atomic transactions."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any
from unittest.mock import AsyncMock
from unittest.mock import MagicMock

import pytest

from postgres_mcp.sql.results import ColumnInfo
from postgres_mcp.sql.sql_driver import SqlDriver
from postgres_mcp.sql.transaction import IsolationLevel
from postgres_mcp.sql.transaction import ResultMode
from postgres_mcp.sql.transaction import TransactionExecutionError
from postgres_mcp.sql.transaction import TransactionStep
from postgres_mcp.sql.transaction import validate_transaction_steps


class AsyncContext:
    """Small async context manager used by cursor and connection fakes."""

    def __init__(self, value: Any):
        self.value = value

    async def __aenter__(self) -> Any:
        return self.value

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        return None


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
    cursor.fetchall = AsyncMock(return_value=list(rows))
    cursor.nextset = AsyncMock(return_value=False)
    return cursor


def make_connection(*cursors: MagicMock) -> MagicMock:
    connection = MagicMock()
    connection.cursor = MagicMock(side_effect=[AsyncContext(cursor) for cursor in cursors])
    connection.commit = AsyncMock()
    connection.rollback = AsyncMock()
    return connection


@pytest.mark.asyncio
async def test_bounded_read_only_query_rolls_back_and_truncates() -> None:
    cursor = make_cursor(
        rows=[{"value": 1}, {"value": 2}],
        description=[Description()],
    )
    connection = make_connection(cursor)
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
    assert result.row_count == 1
    assert result.truncated is True
    assert result.command == "SELECT"
    cursor.execute.assert_any_await("BEGIN TRANSACTION READ ONLY")
    cursor.execute.assert_any_await(
        "SELECT set_config('statement_timeout', %s, true)",
        ["2500ms"],
    )
    cursor.execute.assert_any_await(
        "SELECT set_config('lock_timeout', %s, true)",
        ["2500ms"],
    )
    cursor.execute.assert_any_await("SELECT set_config('row_security', 'on', true)")
    cursor.execute.assert_any_await("SELECT set_config('search_path', 'pg_catalog, public', true)")
    cursor.execute.assert_any_await("SELECT %s::integer AS value", [1])
    cursor.fetchmany.assert_awaited_once_with(2)
    connection.rollback.assert_awaited_once()
    connection.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_bounded_unrestricted_statement_commits_without_rows() -> None:
    cursor = make_cursor(description=None, rowcount=2)
    connection = make_connection(cursor)
    driver = SqlDriver(conn=connection)

    result = await driver.execute_bounded_query(
        "UPDATE items SET active = true WHERE id IN (1, 2)",
        max_rows=10,
        force_readonly=False,
        timeout_seconds=None,
    )

    assert result.rows == []
    assert result.affected_rows == 2
    assert result.command == "UPDATE"
    connection.commit.assert_awaited_once()
    connection.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_bounded_query_rolls_back_on_failure_and_cancellation() -> None:
    failure_cursor = make_cursor(execute_error=RuntimeError("database failure"))
    failure_connection = make_connection(failure_cursor)
    failure_driver = SqlDriver(conn=failure_connection)
    with pytest.raises(RuntimeError, match="database failure"):
        await failure_driver.execute_bounded_query(
            "SELECT 1",
            max_rows=10,
            force_readonly=True,
            timeout_seconds=1,
        )
    failure_connection.rollback.assert_awaited_once()

    cancelled_cursor = make_cursor(execute_error=asyncio.CancelledError())
    cancelled_connection = make_connection(cancelled_cursor)
    cancelled_driver = SqlDriver(conn=cancelled_connection)
    with pytest.raises(asyncio.CancelledError):
        await cancelled_driver._execute_bounded_with_connection(  # pyright: ignore[reportPrivateUsage]
            cancelled_connection,
            "SELECT 1",
            params=None,
            max_rows=10,
            force_readonly=True,
            timeout_seconds=1,
        )
    cancelled_connection.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_bounded_query_validates_public_limits() -> None:
    driver = SqlDriver(conn=MagicMock())
    with pytest.raises(ValueError, match="max_rows"):
        await driver.execute_bounded_query("SELECT 1", max_rows=0, force_readonly=True)
    with pytest.raises(ValueError, match="timeout_seconds"):
        await driver.execute_bounded_query(
            "SELECT 1",
            max_rows=1,
            force_readonly=True,
            timeout_seconds=0,
        )


@pytest.mark.asyncio
async def test_atomic_transaction_commits_all_steps_and_bounds_rows() -> None:
    control = make_cursor()
    select_cursor = make_cursor(
        rows=[{"value": 1}, {"value": 2}],
        description=[Description()],
    )
    update_cursor = make_cursor(
        rows=[{"value": 9}],
        description=[Description()],
        rowcount=1,
    )
    connection = make_connection(control, select_cursor, update_cursor)
    driver = SqlDriver(conn=connection)
    steps = [
        TransactionStep(
            sql="SELECT %s::integer AS value",
            params=(1,),
            result_mode=ResultMode.ROWS,
            max_rows=1,
        ),
        TransactionStep(
            sql="UPDATE public.items SET value = %s WHERE id = %s RETURNING value",
            params=(9, 1),
            expected_rows=1,
            max_affected_rows=1,
            result_mode=ResultMode.ROWS,
            max_rows=10,
        ),
    ]

    result = await driver.execute_transaction(
        steps,
        isolation=IsolationLevel.SERIALIZABLE,
        read_only=False,
        timeout_seconds=5,
        lock_timeout_seconds=1,
    )

    assert result.committed is True
    assert result.isolation is IsolationLevel.SERIALIZABLE
    assert result.steps[0].rows == [{"value": 1}]
    assert result.steps[0].truncated is True
    assert result.steps[1].affected_rows == 1
    assert result.steps[1].rows == [{"value": 9}]
    control.execute.assert_any_await("BEGIN ISOLATION LEVEL SERIALIZABLE READ WRITE")
    control.execute.assert_any_await(
        "SELECT set_config('idle_in_transaction_session_timeout', %s, true)",
        ["5000ms"],
    )
    control.execute.assert_any_await("SELECT set_config('row_security', 'on', true)")
    control.execute.assert_any_await("SELECT set_config('search_path', 'pg_catalog, public', true)")
    select_cursor.execute.assert_awaited_once_with("SELECT %s::integer AS value", [1])
    update_cursor.execute.assert_awaited_once_with(
        "UPDATE public.items SET value = %s WHERE id = %s RETURNING value",
        [9, 1],
    )
    connection.commit.assert_awaited_once()
    connection.rollback.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("rowcount", "expected_rows", "max_rows", "message"),
    [
        (2, 1, 2, "expected 1"),
        (3, None, 2, "maximum is 2"),
        (-1, None, 2, "reliable affected-row count"),
    ],
)
async def test_atomic_transaction_rolls_back_failed_row_guards(
    rowcount: int,
    expected_rows: int | None,
    max_rows: int,
    message: str,
) -> None:
    control = make_cursor()
    update_cursor = make_cursor(rowcount=rowcount)
    connection = make_connection(control, update_cursor)
    driver = SqlDriver(conn=connection)
    steps = [
        TransactionStep(
            sql="UPDATE public.items SET value = 1 WHERE id > 0",
            expected_rows=expected_rows,
            max_affected_rows=max_rows,
        )
    ]

    with pytest.raises(TransactionExecutionError, match=message) as error:
        await driver.execute_transaction(
            steps,
            isolation=IsolationLevel.READ_COMMITTED,
            read_only=False,
            timeout_seconds=5,
            lock_timeout_seconds=1,
        )

    assert error.value.failed_step == 0
    connection.rollback.assert_awaited_once()
    connection.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_atomic_transaction_wraps_database_errors_and_commit_failures() -> None:
    control = make_cursor()
    failed_step = make_cursor(execute_error=RuntimeError("broken query"))
    connection = make_connection(control, failed_step)
    driver = SqlDriver(conn=connection)
    steps = [TransactionStep(sql="SELECT 1")]

    with pytest.raises(TransactionExecutionError, match="transaction failed: broken query") as error:
        await driver.execute_transaction(
            steps,
            isolation=IsolationLevel.READ_COMMITTED,
            read_only=True,
            timeout_seconds=5,
            lock_timeout_seconds=1,
        )
    assert error.value.failed_step == 0
    connection.rollback.assert_awaited_once()

    control = make_cursor()
    successful_step = make_cursor()
    commit_connection = make_connection(control, successful_step)
    commit_connection.commit.side_effect = RuntimeError("commit failed")
    commit_driver = SqlDriver(conn=commit_connection)
    with pytest.raises(TransactionExecutionError, match="commit failed"):
        await commit_driver.execute_transaction(
            steps,
            isolation=IsolationLevel.READ_COMMITTED,
            read_only=True,
            timeout_seconds=5,
            lock_timeout_seconds=1,
        )
    commit_connection.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_atomic_transaction_rolls_back_before_propagating_cancellation() -> None:
    control = make_cursor()
    cancelled_step = make_cursor(execute_error=asyncio.CancelledError())
    connection = make_connection(control, cancelled_step)
    driver = SqlDriver(conn=connection)
    validated = validate_transaction_steps(
        [TransactionStep(sql="SELECT 1")],
        read_only=True,
        absolute_max_rows=100,
    )

    with pytest.raises(asyncio.CancelledError):
        await driver._execute_transaction_with_connection(  # pyright: ignore[reportPrivateUsage]
            connection,
            validated,
            isolation=IsolationLevel.READ_COMMITTED,
            read_only=True,
            timeout_seconds=5,
            lock_timeout_seconds=1,
        )
    connection.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_atomic_transaction_validates_limits_and_wraps_timeout() -> None:
    driver = SqlDriver(conn=MagicMock())
    steps = [TransactionStep(sql="SELECT 1")]
    with pytest.raises(ValueError, match="timeout_seconds"):
        await driver.execute_transaction(
            steps,
            isolation=IsolationLevel.READ_COMMITTED,
            read_only=True,
            timeout_seconds=0,
            lock_timeout_seconds=1,
        )
    with pytest.raises(ValueError, match="lock_timeout_seconds"):
        await driver.execute_transaction(
            steps,
            isolation=IsolationLevel.READ_COMMITTED,
            read_only=True,
            timeout_seconds=1,
            lock_timeout_seconds=0,
        )

    driver._execute_transaction_with_connection = AsyncMock(  # pyright: ignore[reportPrivateUsage]
        side_effect=TimeoutError
    )
    with pytest.raises(TransactionExecutionError, match="timed out"):
        await driver.execute_transaction(
            steps,
            isolation=IsolationLevel.READ_COMMITTED,
            read_only=True,
            timeout_seconds=1,
            lock_timeout_seconds=1,
        )
