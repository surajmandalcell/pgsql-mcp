"""Infrastructure-adapter contracts for guarded PostgreSQL data operations."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from typing import cast
from unittest.mock import AsyncMock

import pytest
from psycopg import OperationalError

from postgres_mcp.data_ops import DataConflictError
from postgres_mcp.data_ops import DataExecutionError
from postgres_mcp.data_ops import PostgresDataRepository
from postgres_mcp.sql import SqlDriver


class FakeCursor:
    def __init__(self) -> None:
        self.execute = AsyncMock()

    async def __aenter__(self) -> FakeCursor:
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        return None


class FakeConnection:
    def __init__(
        self,
        cursor: FakeCursor,
        *,
        commit_error: Exception | None = None,
        rollback_error: Exception | None = None,
    ) -> None:
        self._cursor = cursor
        self.commit = AsyncMock(side_effect=commit_error)
        self.rollback = AsyncMock(side_effect=rollback_error)

    def cursor(self, **_kwargs: Any) -> FakeCursor:
        return self._cursor


class FakeDriver:
    def __init__(self, connection: FakeConnection) -> None:
        self._connection = connection

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[FakeConnection]:
        yield self._connection


class TestableRepository(PostgresDataRepository):
    async def run_transaction(self, *, read_only: bool, operation: Any) -> Any:
        return await self._transaction(read_only=read_only, operation=operation)

    def bounded_page(
        self, rows: list[dict[str, Any]], *, limit: int, hidden: dict[str, str]
    ) -> tuple[list[dict[str, Any]], int | None, str | None]:
        return self._bounded_page_rows(rows, limit=limit, hidden=hidden)


def repository(connection: FakeConnection) -> TestableRepository:
    return TestableRepository(cast(SqlDriver, FakeDriver(connection)), timeout_seconds=1, lock_timeout_seconds=1)


@pytest.mark.asyncio
async def test_read_only_operation_uses_hardened_transaction_and_rolls_back() -> None:
    cursor = FakeCursor()
    connection = FakeConnection(cursor)
    adapter = repository(connection)

    result = await adapter.run_transaction(read_only=True, operation=AsyncMock(return_value="ok"))

    assert result == "ok"
    connection.rollback.assert_awaited_once()
    connection.commit.assert_not_awaited()
    executed = [call.args[0] for call in cursor.execute.await_args_list]
    assert executed[0] == "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY"
    assert "SELECT set_config('search_path', 'pg_catalog', true)" in executed
    assert "SELECT set_config('application_name', 'pgsql-mcp:data-operations', true)" in executed


@pytest.mark.asyncio
async def test_expected_conflict_is_rolled_back_without_losing_domain_error() -> None:
    connection = FakeConnection(FakeCursor())
    adapter = repository(connection)

    async def conflict(_cursor: Any) -> None:
        raise DataConflictError("stale version")

    with pytest.raises(DataConflictError, match="stale version"):
        await adapter.run_transaction(read_only=False, operation=conflict)

    connection.rollback.assert_awaited_once()
    connection.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_database_failure_is_redacted_after_confirmed_rollback() -> None:
    connection = FakeConnection(FakeCursor())
    adapter = repository(connection)

    async def fail(_cursor: Any) -> None:
        raise RuntimeError("sensitive database detail")

    with pytest.raises(DataExecutionError) as error:
        await adapter.run_transaction(read_only=False, operation=fail)

    assert str(error.value) == "PostgreSQL data operation failed and was rolled back"
    assert error.value.commit_state == "not_committed"
    assert error.value.rolled_back is True
    connection.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_connection_loss_during_commit_reports_unknown_state() -> None:
    connection = FakeConnection(FakeCursor(), commit_error=OperationalError("connection lost"))
    adapter = repository(connection)

    with pytest.raises(DataExecutionError) as error:
        await adapter.run_transaction(read_only=False, operation=AsyncMock(return_value=None))

    assert error.value.commit_state == "unknown"
    assert "verify database state" in str(error.value)
    connection.commit.assert_awaited_once()
    connection.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancellation_attempts_rollback_and_propagates() -> None:
    connection = FakeConnection(FakeCursor())
    adapter = repository(connection)

    async def cancel(_cursor: Any) -> None:
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await adapter.run_transaction(read_only=False, operation=cancel)

    connection.rollback.assert_awaited_once()


def test_page_rows_are_bounded_by_encoded_response_bytes() -> None:
    adapter = repository(FakeConnection(FakeCursor()))
    rows = [
        {"id": 1, "payload": "x" * 300_000},
        {"id": 2, "payload": "y" * 300_000},
    ]

    visible, stop_index, reason = adapter.bounded_page(rows, limit=2, hidden={})

    assert [row["id"] for row in visible] == [1]
    assert stop_index == 1
    assert reason == "byte_limit"
