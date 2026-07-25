"""Failure-path contracts for the reviewed-migration PostgreSQL adapter."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from typing import cast
from unittest.mock import AsyncMock

import pytest

from postgres_mcp.migrations.domain import MigrationConflictError
from postgres_mcp.migrations.domain import MigrationExecutionError
from postgres_mcp.migrations.domain import MigrationOperationStatus
from postgres_mcp.migrations.domain import MigrationReviewMismatch
from postgres_mcp.migrations.domain import MigrationStepDraft
from postgres_mcp.migrations.planner import MigrationPlanner
from postgres_mcp.migrations.postgres_backend import PostgresMigrationBackend
from postgres_mcp.sql import SqlDriver


class FakeCursor:
    def __init__(
        self,
        *,
        one: Any = None,
        all_rows: list[Any] | None = None,
        execute_error: BaseException | None = None,
    ) -> None:
        self.one = one
        self.all_rows = all_rows or []
        self.execute = AsyncMock(side_effect=execute_error)

    async def __aenter__(self) -> FakeCursor:
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        return None

    async def fetchone(self) -> Any:
        return self.one

    async def fetchall(self) -> list[Any]:
        return self.all_rows


class FakeConnection:
    def __init__(
        self,
        cursors: list[FakeCursor],
        *,
        commit_error: BaseException | None = None,
        rollback_error: BaseException | None = None,
    ) -> None:
        self.cursors = iter(cursors)
        self.commit = AsyncMock(side_effect=commit_error)
        self.rollback = AsyncMock(side_effect=rollback_error)

    def cursor(self, **_kwargs: Any) -> FakeCursor:
        return next(self.cursors)


class FakeDriver:
    def __init__(self, connection: FakeConnection) -> None:
        self._connection = connection

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[FakeConnection]:
        yield self._connection


def plan():
    return MigrationPlanner().create_plan(
        name="edge-migration",
        steps=[MigrationStepDraft("CREATE TABLE app.items(id integer)", "DROP TABLE app.items")],
    )


def relation() -> dict[str, Any]:
    return {
        "oid": 42,
        "relkind": "r",
        "relpersistence": "p",
        "relispartition": False,
        "relrowsecurity": False,
        "relforcerowsecurity": False,
        "owned_by_current_user": True,
        "trigger_count": 0,
        "rule_count": 0,
    }


def columns() -> list[dict[str, Any]]:
    definitions = (
        ("id", 20, True, "a"),
        ("name", 25, True, ""),
        ("checksum", 25, True, ""),
        ("review_hash", 25, True, ""),
        ("plan_version", 23, True, ""),
        ("batch", 23, True, ""),
        ("step_count", 23, True, ""),
        ("plan", 3802, True, ""),
        ("applied_at", 1184, True, ""),
        ("applied_by", 19, True, ""),
    )
    return [
        {
            "name": name,
            "type_oid": oid,
            "not_null": not_null,
            "identity_kind": identity,
        }
        for name, oid, not_null, identity in definitions
    ]


def row(reviewed_plan, *, migration_id: int = 7) -> dict[str, Any]:
    return {
        "migration_id": migration_id,
        "latest_migration_id": migration_id,
        "name": reviewed_plan.name,
        "checksum": reviewed_plan.checksum,
        "review_hash": reviewed_plan.review_hash,
        "plan_version": reviewed_plan.plan_version,
        "batch": 1,
        "step_count": len(reviewed_plan.steps),
        "plan": reviewed_plan.canonical_payload(),
        "applied_at": "2026-07-25T00:00:00+00:00",
        "applied_by": "postgres",
    }


def backend(connection: FakeConnection) -> PostgresMigrationBackend:
    return PostgresMigrationBackend(cast(SqlDriver, FakeDriver(connection)))


@pytest.mark.asyncio
async def test_apply_conflict_before_transaction_start_does_not_attempt_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = FakeConnection([])
    adapter = backend(connection)
    monkeypatch.setattr(
        adapter,
        "_begin",
        AsyncMock(side_effect=MigrationConflictError("preflight conflict")),
    )

    with pytest.raises(MigrationConflictError, match="preflight conflict"):
        await adapter.apply(plan(), timeout_seconds=30, lock_timeout_seconds=5)

    connection.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_apply_cancellation_rolls_back_and_is_not_wrapped() -> None:
    reviewed = plan()
    connection = FakeConnection(
        [
            FakeCursor(),
            FakeCursor(),
            FakeCursor(one=relation()),
            FakeCursor(all_rows=columns()),
            FakeCursor(one=None),
            FakeCursor(one={"batch": 1}),
            FakeCursor(execute_error=asyncio.CancelledError()),
        ]
    )

    with pytest.raises(asyncio.CancelledError):
        await backend(connection).apply(reviewed, timeout_seconds=30, lock_timeout_seconds=5)

    connection.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_apply_outer_timeout_has_explicit_noncommit_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = FakeConnection([])
    adapter = backend(connection)

    async def block(*_args: Any, **_kwargs: Any) -> None:
        await asyncio.sleep(1)

    monkeypatch.setattr(adapter, "_begin", block)

    with pytest.raises(MigrationExecutionError, match="apply timed out") as error:
        await adapter.apply(plan(), timeout_seconds=0, lock_timeout_seconds=0)

    assert error.value.phase == "apply"
    assert error.value.commit_state == "not_committed"


@pytest.mark.asyncio
async def test_rollback_without_ledger_is_idempotent() -> None:
    connection = FakeConnection([FakeCursor(), FakeCursor(one=(False,))])

    result = await backend(connection).rollback(
        name=plan().name,
        review_hash=plan().review_hash,
        timeout_seconds=30,
        lock_timeout_seconds=5,
    )

    assert result.status is MigrationOperationStatus.ALREADY_ROLLED_BACK
    connection.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_rollback_review_mismatch_rolls_back_before_rejection() -> None:
    reviewed = plan()
    connection = FakeConnection(
        [
            FakeCursor(),
            FakeCursor(one=(True,)),
            FakeCursor(one=relation()),
            FakeCursor(all_rows=columns()),
            FakeCursor(one=row(reviewed)),
        ]
    )

    with pytest.raises(MigrationReviewMismatch, match="does not match"):
        await backend(connection).rollback(
            name=reviewed.name,
            review_hash="0" * 64,
            timeout_seconds=30,
            lock_timeout_seconds=5,
        )

    connection.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_rollback_commit_failure_reports_unknown_state() -> None:
    reviewed = plan()
    connection = FakeConnection(
        [
            FakeCursor(),
            FakeCursor(one=(True,)),
            FakeCursor(one=relation()),
            FakeCursor(all_rows=columns()),
            FakeCursor(one=row(reviewed)),
            FakeCursor(),
            FakeCursor(),
        ],
        commit_error=RuntimeError("connection lost"),
    )

    with pytest.raises(MigrationExecutionError, match="outcome is unknown") as error:
        await backend(connection).rollback(
            name=reviewed.name,
            review_hash=reviewed.review_hash,
            timeout_seconds=30,
            lock_timeout_seconds=5,
        )

    assert error.value.phase == "commit"
    assert error.value.commit_state == "unknown"
    connection.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_rollback_cancellation_rolls_back_and_is_not_wrapped() -> None:
    reviewed = plan()
    connection = FakeConnection(
        [
            FakeCursor(),
            FakeCursor(one=(True,)),
            FakeCursor(one=relation()),
            FakeCursor(all_rows=columns()),
            FakeCursor(one=row(reviewed)),
            FakeCursor(execute_error=asyncio.CancelledError()),
        ]
    )

    with pytest.raises(asyncio.CancelledError):
        await backend(connection).rollback(
            name=reviewed.name,
            review_hash=reviewed.review_hash,
            timeout_seconds=30,
            lock_timeout_seconds=5,
        )

    connection.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_rollback_outer_timeout_has_explicit_noncommit_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = FakeConnection([])
    adapter = backend(connection)

    async def block(*_args: Any, **_kwargs: Any) -> None:
        await asyncio.sleep(1)

    monkeypatch.setattr(adapter, "_begin", block)

    with pytest.raises(MigrationExecutionError, match="rollback timed out") as error:
        await adapter.rollback(
            name=plan().name,
            review_hash=plan().review_hash,
            timeout_seconds=0,
            lock_timeout_seconds=0,
        )

    assert error.value.phase == "rollback"
    assert error.value.commit_state == "not_committed"


@pytest.mark.asyncio
async def test_status_failure_after_begin_attempts_rollback() -> None:
    connection = FakeConnection([FakeCursor(), FakeCursor(execute_error=RuntimeError("catalog unavailable"))])

    with pytest.raises(RuntimeError, match="catalog unavailable"):
        await backend(connection).status(limit=10)

    connection.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_ledger_helpers_cover_mapping_and_empty_results() -> None:
    adapter = backend(FakeConnection([]))

    assert await adapter._ledger_exists(  # pyright: ignore[reportPrivateUsage]
        FakeConnection([FakeCursor(one={"exists": True})])
    )
    assert await adapter._next_batch(  # pyright: ignore[reportPrivateUsage]
        FakeConnection([FakeCursor(one=None)])
    ) == 1


@pytest.mark.asyncio
async def test_column_contract_and_missing_insert_result_are_rejected() -> None:
    adapter = backend(FakeConnection([]))
    invalid_columns = columns()
    invalid_columns[-1] = {**invalid_columns[-1], "not_null": False}

    with pytest.raises(MigrationConflictError, match="trusted ledger contract"):
        await adapter._validate_ledger(  # pyright: ignore[reportPrivateUsage]
            FakeConnection([FakeCursor(one=relation()), FakeCursor(all_rows=invalid_columns)])
        )

    with pytest.raises(RuntimeError, match="did not return a row"):
        await adapter._insert_ledger(  # pyright: ignore[reportPrivateUsage]
            FakeConnection([FakeCursor(one=None)]),
            plan(),
            batch=1,
        )


def test_nonmapping_rows_are_never_trusted() -> None:
    from postgres_mcp.migrations import postgres_backend

    assert postgres_backend._mapping(("unexpected",)) == {}  # pyright: ignore[reportPrivateUsage]
