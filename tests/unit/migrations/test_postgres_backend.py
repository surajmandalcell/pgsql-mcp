"""Infrastructure-adapter contracts independent of a running PostgreSQL server."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from typing import cast
from unittest.mock import AsyncMock

import pytest

from postgres_mcp.migrations.domain import MigrationConflictError
from postgres_mcp.migrations.domain import MigrationExecutionError
from postgres_mcp.migrations.domain import MigrationOperationStatus
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
        execute_error: Exception | None = None,
    ):
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
        commit_error: Exception | None = None,
        rollback_error: Exception | None = None,
    ):
        self.cursors = iter(cursors)
        self.commit = AsyncMock(side_effect=commit_error)
        self.rollback = AsyncMock(side_effect=rollback_error)

    def cursor(self, **_kwargs: Any) -> FakeCursor:
        return next(self.cursors)


class FakeDriver:
    def __init__(self, connection: FakeConnection):
        self._connection = connection

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[FakeConnection]:
        yield self._connection


def migration(table: str = "items"):
    return MigrationPlanner().create_plan(
        name="create-items",
        steps=[MigrationStepDraft(f"CREATE TABLE app.{table}(id integer)", f"DROP TABLE app.{table}")],
    )


def ledger_relation(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
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
    row.update(overrides)
    return row


def ledger_columns() -> list[dict[str, Any]]:
    definitions = [
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
    ]
    return [{"name": name, "type_oid": oid, "not_null": not_null, "identity_kind": identity} for name, oid, not_null, identity in definitions]


def ledger_row(plan, *, migration_id: int = 7) -> dict[str, Any]:
    return {
        "migration_id": migration_id,
        "name": plan.name,
        "checksum": plan.checksum,
        "review_hash": plan.review_hash,
        "plan_version": plan.plan_version,
        "batch": 2,
        "step_count": len(plan.steps),
        "plan": plan.canonical_payload(),
        "applied_at": "2026-07-24T00:00:00+00:00",
        "applied_by": "postgres",
    }


def test_backend_rejects_unsafe_ledger_schema() -> None:
    driver = cast(SqlDriver, FakeDriver(FakeConnection([])))
    with pytest.raises(ValueError, match="unquoted PostgreSQL identifier"):
        PostgresMigrationBackend(driver, ledger_schema="public; DROP SCHEMA public")


@pytest.mark.asyncio
async def test_apply_is_idempotent_only_for_matching_verified_ledger_plan() -> None:
    plan = migration()
    connection = FakeConnection(
        [
            FakeCursor(),  # configure transaction
            FakeCursor(),  # create ledger and index
            FakeCursor(one=ledger_relation()),
            FakeCursor(all_rows=ledger_columns()),
            FakeCursor(one=ledger_row(plan)),
        ]
    )
    backend = PostgresMigrationBackend(cast(SqlDriver, FakeDriver(connection)))

    result = await backend.apply(plan, timeout_seconds=30, lock_timeout_seconds=5)

    assert result.status is MigrationOperationStatus.ALREADY_APPLIED
    assert result.migration is not None and result.migration.migration_id == 7
    connection.commit.assert_awaited_once()
    connection.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_apply_rolls_back_when_same_name_has_different_valid_reviewed_plan() -> None:
    requested = migration("items")
    stored = migration("different_items")
    connection = FakeConnection(
        [
            FakeCursor(),
            FakeCursor(),
            FakeCursor(one=ledger_relation()),
            FakeCursor(all_rows=ledger_columns()),
            FakeCursor(one=ledger_row(stored)),
        ]
    )
    backend = PostgresMigrationBackend(cast(SqlDriver, FakeDriver(connection)))

    with pytest.raises(MigrationConflictError, match="different reviewed content"):
        await backend.apply(requested, timeout_seconds=30, lock_timeout_seconds=5)

    connection.rollback.assert_awaited_once()
    connection.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_status_is_empty_without_a_ledger() -> None:
    connection = FakeConnection(
        [
            FakeCursor(),  # configure transaction
            FakeCursor(one=(False,)),  # to_regclass
        ]
    )
    backend = PostgresMigrationBackend(cast(SqlDriver, FakeDriver(connection)))

    snapshot = await backend.status(limit=10)

    assert snapshot.migrations == ()
    connection.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_spoofed_or_modified_ledger_is_rejected_before_reading_entries() -> None:
    connection = FakeConnection(
        [
            FakeCursor(),
            FakeCursor(one=(True,)),
            FakeCursor(one=ledger_relation(trigger_count=1)),
        ]
    )
    backend = PostgresMigrationBackend(cast(SqlDriver, FakeDriver(connection)))

    with pytest.raises(MigrationConflictError, match="trusted ledger contract"):
        await backend.status(limit=10)

    connection.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_commit_failure_reports_ambiguous_state_instead_of_claiming_rollback() -> None:
    plan = migration()
    inserted = ledger_row(plan)
    connection = FakeConnection(
        [
            FakeCursor(),
            FakeCursor(),
            FakeCursor(one=ledger_relation()),
            FakeCursor(all_rows=ledger_columns()),
            FakeCursor(one=None),
            FakeCursor(one=(1,)),
            FakeCursor(),
            FakeCursor(one=inserted),
        ],
        commit_error=RuntimeError("connection lost during commit"),
    )
    backend = PostgresMigrationBackend(cast(SqlDriver, FakeDriver(connection)))

    with pytest.raises(MigrationExecutionError) as error:
        await backend.apply(plan, timeout_seconds=30, lock_timeout_seconds=5)

    assert error.value.commit_state == "unknown"
    assert error.value.rolled_back is False
    assert error.value.failed_step == 0
    connection.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_failed_rollback_is_not_reported_as_confirmed() -> None:
    plan = migration()
    connection = FakeConnection(
        [
            FakeCursor(),
            FakeCursor(one=(True,)),
            FakeCursor(one=ledger_relation()),
            FakeCursor(all_rows=ledger_columns()),
            FakeCursor(one=ledger_row(plan)),
            FakeCursor(execute_error=RuntimeError("DDL failed")),
        ],
        rollback_error=RuntimeError("rollback failed"),
    )
    backend = PostgresMigrationBackend(cast(SqlDriver, FakeDriver(connection)))

    with pytest.raises(MigrationExecutionError) as error:
        await backend.rollback(
            name=plan.name,
            review_hash=plan.review_hash,
            timeout_seconds=30,
            lock_timeout_seconds=5,
        )

    assert error.value.commit_state == "not_committed"
    assert error.value.rolled_back is False
    assert error.value.failed_step == 0
