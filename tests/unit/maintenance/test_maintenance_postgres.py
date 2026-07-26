"""PostgreSQL-adapter contracts for reviewed maintenance."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from typing import cast
from unittest.mock import AsyncMock
from unittest.mock import Mock

import pytest

from postgres_mcp.maintenance import MaintenanceBusyError
from postgres_mcp.maintenance import MaintenanceConflictError
from postgres_mcp.maintenance import MaintenanceExecutionError
from postgres_mcp.maintenance import MaintenanceOperation
from postgres_mcp.maintenance import MaintenanceOperationStatus
from postgres_mcp.maintenance import MaintenanceOptions
from postgres_mcp.maintenance import MaintenancePlanner
from postgres_mcp.maintenance import MaintenanceRequest
from postgres_mcp.maintenance import MaintenanceReviewMismatch
from postgres_mcp.maintenance import MaintenanceTarget
from postgres_mcp.maintenance import PostgresMaintenanceBackend
from postgres_mcp.maintenance import ReconciliationResolution
from postgres_mcp.maintenance import TargetSnapshot
from postgres_mcp.sql import SqlDriver


class FakeCursor:
    def __init__(
        self,
        *,
        one: Any = None,
        rows: list[Any] | None = None,
        execute_error: BaseException | None = None,
    ) -> None:
        self.one = one
        self.rows = rows or []
        self.execute = AsyncMock(side_effect=execute_error)

    async def __aenter__(self) -> FakeCursor:
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        return None

    async def fetchone(self) -> Any:
        return self.one

    async def fetchall(self) -> list[Any]:
        return self.rows


class FakeConnection:
    def __init__(
        self,
        cursors: list[FakeCursor] | None = None,
        *,
        autocommit: bool = False,
    ) -> None:
        self._cursors = iter(cursors or [])
        self.autocommit = autocommit
        self.set_autocommit = AsyncMock(side_effect=self._set_autocommit)
        self.rollback = AsyncMock()

    async def _set_autocommit(self, value: bool) -> None:
        self.autocommit = value

    def cursor(self, **_kwargs: Any) -> FakeCursor:
        return next(self._cursors)


class FakeDriver:
    def __init__(self, connection: FakeConnection) -> None:
        self._connection = connection
        self.conn = object()

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[FakeConnection]:
        yield self._connection


def backend(connection: FakeConnection) -> PostgresMaintenanceBackend:
    return PostgresMaintenanceBackend(cast(SqlDriver, FakeDriver(connection)))


def request(operation: MaintenanceOperation = MaintenanceOperation.VACUUM_ANALYZE) -> MaintenanceRequest:
    return MaintenanceRequest(
        name="nightly-items-maintenance",
        operation=operation,
        target=MaintenanceTarget("app", "items"),
    )


def snapshot(
    *,
    oid: int = 42,
    kind: str = "r",
    populated: bool = True,
    unique_index: bool = False,
    exclusion_index: bool = False,
) -> TargetSnapshot:
    return TargetSnapshot(
        oid=oid,
        relation_kind=kind,
        persistence="p",
        is_partition=False,
        is_populated=populated,
        has_usable_unique_index=unique_index,
        is_exclusion_index=exclusion_index,
    )


def reviewed_plan(operation: MaintenanceOperation = MaintenanceOperation.VACUUM_ANALYZE):
    target_snapshot = snapshot(
        kind=(
            "i"
            if operation is MaintenanceOperation.REINDEX_INDEX_CONCURRENTLY
            else "m"
            if operation is MaintenanceOperation.REFRESH_MATERIALIZED_VIEW_CONCURRENTLY
            else "r"
        ),
        unique_index=operation is MaintenanceOperation.REFRESH_MATERIALIZED_VIEW_CONCURRENTLY,
    )
    return MaintenancePlanner().create_plan(request(operation), target_snapshot)


def record_row(plan, *, status: MaintenanceOperationStatus = MaintenanceOperationStatus.RUNNING) -> dict[str, Any]:
    return {
        "id": 7,
        "name": plan.name,
        "review_hash": plan.review_hash,
        "plan_version": plan.plan_version,
        "operation": plan.operation.value,
        "target_schema": plan.target.schema,
        "target_name": plan.target.name,
        "target_oid": plan.target_oid,
        "plan": plan.canonical_payload(),
        "status": status.value,
        "started_at": "2026-07-25T00:00:00+00:00",
        "finished_at": None,
        "error_code": None,
        "applied_by": "postgres",
    }


def configure_apply(
    adapter: PostgresMaintenanceBackend,
    monkeypatch: pytest.MonkeyPatch,
    *,
    plan,
    existing: dict[str, Any] | None = None,
    execute_error: BaseException | None = None,
) -> tuple[FakeConnection, FakeCursor, AsyncMock, AsyncMock]:
    cursor = FakeCursor(execute_error=execute_error)
    connection = cast(FakeDriver, adapter.sql_driver)._connection
    connection._cursors = iter([cursor])
    finish = AsyncMock(return_value=record_row(plan, status=MaintenanceOperationStatus.SUCCEEDED))
    unknown = AsyncMock()
    monkeypatch.setattr(adapter, "_configure_session", AsyncMock())
    monkeypatch.setattr(adapter, "_acquire_lock", AsyncMock(return_value=True))
    monkeypatch.setattr(
        adapter,
        "_inspect_on_connection",
        AsyncMock(
            return_value=snapshot(
                oid=plan.target_oid,
                kind=plan.target_kind,
                populated=bool(plan.preconditions["is_populated"]),
                unique_index=bool(plan.preconditions["has_usable_unique_index"]),
                exclusion_index=bool(plan.preconditions["is_exclusion_index"]),
            )
        ),
    )
    monkeypatch.setattr(adapter, "_ledger_exists", AsyncMock(return_value=existing is not None))
    monkeypatch.setattr(adapter, "_ensure_ledger", AsyncMock())
    monkeypatch.setattr(adapter, "_validate_ledger", AsyncMock())
    monkeypatch.setattr(adapter, "_get_by_name", AsyncMock(return_value=existing))
    monkeypatch.setattr(adapter, "_insert_running_record", AsyncMock(return_value=record_row(plan)))
    monkeypatch.setattr(adapter, "_restart_record", AsyncMock(return_value=record_row(plan)))
    monkeypatch.setattr(adapter, "_finish_record", finish)
    monkeypatch.setattr(adapter, "_best_effort_finish_unknown", unknown)
    monkeypatch.setattr(adapter, "_cleanup_session", AsyncMock(return_value=None))
    return connection, cursor, finish, unknown


def test_constructor_rejects_unsafe_ledger_identifiers() -> None:
    with pytest.raises(ValueError, match="ledger_schema"):
        PostgresMaintenanceBackend(cast(SqlDriver, object()), ledger_schema="public; DROP SCHEMA public")


def test_command_builder_has_no_raw_sql_escape_hatch() -> None:
    vacuum_plan = MaintenancePlanner().create_plan(
        MaintenanceRequest(
            name="vacuum-items",
            operation=MaintenanceOperation.VACUUM_ANALYZE,
            target=MaintenanceTarget("app", "items"),
            options=MaintenanceOptions(skip_locked=True, index_cleanup="off", parallel=2),
        ),
        snapshot(),
    )
    analyze_plan = reviewed_plan(MaintenanceOperation.ANALYZE)
    reindex_plan = reviewed_plan(MaintenanceOperation.REINDEX_INDEX_CONCURRENTLY)
    refresh_plan = reviewed_plan(MaintenanceOperation.REFRESH_MATERIALIZED_VIEW_CONCURRENTLY)

    assert PostgresMaintenanceBackend._build_command(vacuum_plan).as_string(None) == (  # pyright: ignore[reportPrivateUsage]
        'VACUUM (ANALYZE, SKIP_LOCKED, INDEX_CLEANUP OFF, PARALLEL 2) "app"."items"'
    )
    assert PostgresMaintenanceBackend._build_command(analyze_plan).as_string(None) == (  # pyright: ignore[reportPrivateUsage]
        'ANALYZE "app"."items"'
    )
    assert PostgresMaintenanceBackend._build_command(reindex_plan).as_string(None) == (  # pyright: ignore[reportPrivateUsage]
        'REINDEX INDEX CONCURRENTLY "app"."items"'
    )
    assert PostgresMaintenanceBackend._build_command(refresh_plan).as_string(None) == (  # pyright: ignore[reportPrivateUsage]
        'REFRESH MATERIALIZED VIEW CONCURRENTLY "app"."items"'
    )


@pytest.mark.asyncio
async def test_inspection_returns_live_catalog_snapshot_and_rolls_back() -> None:
    cursor = FakeCursor(
        one={
            "oid": 42,
            "relation_kind": "r",
            "persistence": "p",
            "relispartition": False,
            "relispopulated": True,
            "has_usable_unique_index": False,
            "is_exclusion_index": False,
        }
    )
    connection = FakeConnection([cursor])

    result = await backend(connection).inspect(request())

    assert result == snapshot()
    connection.rollback.assert_awaited_once()
    cursor.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_inspection_rejects_missing_target() -> None:
    connection = FakeConnection([FakeCursor(one=None)])

    with pytest.raises(Exception, match="does not exist"):
        await backend(connection).inspect(request())

    connection.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_apply_executes_reviewed_command_in_autocommit_and_restores_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = reviewed_plan()
    connection = FakeConnection([])
    adapter = backend(connection)
    connection, cursor, finish, _unknown = configure_apply(adapter, monkeypatch, plan=plan)

    result = await adapter.apply(plan, timeout_seconds=30, lock_timeout_seconds=5)

    assert result.status is MaintenanceOperationStatus.SUCCEEDED
    connection.set_autocommit.assert_any_await(True)
    cursor.execute.assert_awaited_once()
    finish.assert_awaited_once()


@pytest.mark.asyncio
async def test_apply_is_idempotent_for_a_succeeded_reviewed_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = reviewed_plan()
    existing = record_row(plan, status=MaintenanceOperationStatus.SUCCEEDED)
    adapter = backend(FakeConnection([]))
    _connection, cursor, finish, _unknown = configure_apply(
        adapter,
        monkeypatch,
        plan=plan,
        existing=existing,
    )

    inspect = AsyncMock(return_value=snapshot(oid=99))
    monkeypatch.setattr(adapter, "_inspect_on_connection", inspect)

    result = await adapter.apply(plan, timeout_seconds=30, lock_timeout_seconds=5)

    assert result.status is MaintenanceOperationStatus.ALREADY_SUCCEEDED
    inspect.assert_not_awaited()
    cursor.execute.assert_not_awaited()
    finish.assert_not_awaited()


@pytest.mark.asyncio
async def test_apply_rejects_unresolved_or_changed_durable_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = reviewed_plan()
    adapter = backend(FakeConnection([]))
    configure_apply(
        adapter,
        monkeypatch,
        plan=plan,
        existing=record_row(plan, status=MaintenanceOperationStatus.UNKNOWN),
    )

    with pytest.raises(MaintenanceConflictError, match="unresolved"):
        await adapter.apply(plan, timeout_seconds=30, lock_timeout_seconds=5)

    changed = reviewed_plan(MaintenanceOperation.ANALYZE)
    configure_apply(
        adapter,
        monkeypatch,
        plan=plan,
        existing=record_row(changed, status=MaintenanceOperationStatus.SUCCEEDED),
    )
    with pytest.raises(MaintenanceConflictError, match="different reviewed content"):
        await adapter.apply(plan, timeout_seconds=30, lock_timeout_seconds=5)


@pytest.mark.asyncio
async def test_apply_failure_is_durable_and_redacted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = reviewed_plan()
    adapter = backend(FakeConnection([]))
    _connection, _cursor, finish, _unknown = configure_apply(
        adapter,
        monkeypatch,
        plan=plan,
        execute_error=RuntimeError("secret database detail"),
    )
    finish.return_value = record_row(plan, status=MaintenanceOperationStatus.FAILED)

    with pytest.raises(MaintenanceExecutionError, match="maintenance operation failed") as error:
        await adapter.apply(plan, timeout_seconds=30, lock_timeout_seconds=5)

    assert error.value.outcome == "failed"
    assert "secret database detail" not in str(error.value)
    assert finish.await_args_list[-1].kwargs["status"] is MaintenanceOperationStatus.FAILED


@pytest.mark.asyncio
async def test_apply_cancellation_marks_unknown_before_propagating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = reviewed_plan()
    adapter = backend(FakeConnection([]))
    _connection, _cursor, _finish, unknown = configure_apply(
        adapter,
        monkeypatch,
        plan=plan,
        execute_error=asyncio.CancelledError(),
    )

    with pytest.raises(asyncio.CancelledError):
        await adapter.apply(plan, timeout_seconds=30, lock_timeout_seconds=5)

    unknown.assert_awaited_once_with(cast(Any, adapter.sql_driver)._connection, plan.name, "cancelled")


@pytest.mark.asyncio
async def test_apply_lock_conflict_prevents_ledger_or_command_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = reviewed_plan()
    connection = FakeConnection([])
    adapter = backend(connection)
    monkeypatch.setattr(adapter, "_configure_session", AsyncMock())
    monkeypatch.setattr(adapter, "_acquire_lock", AsyncMock(return_value=False))
    monkeypatch.setattr(adapter, "_cleanup_session", AsyncMock(return_value=None))
    ensure = AsyncMock()
    monkeypatch.setattr(adapter, "_ensure_ledger", ensure)

    with pytest.raises(MaintenanceBusyError, match="target lock"):
        await adapter.apply(plan, timeout_seconds=30, lock_timeout_seconds=5)

    ensure.assert_not_awaited()


@pytest.mark.asyncio
async def test_apply_detects_target_drift_before_ledger_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = reviewed_plan()
    adapter = backend(FakeConnection([]))
    monkeypatch.setattr(adapter, "_configure_session", AsyncMock())
    monkeypatch.setattr(adapter, "_acquire_lock", AsyncMock(return_value=True))
    monkeypatch.setattr(adapter, "_ledger_exists", AsyncMock(return_value=False))
    monkeypatch.setattr(adapter, "_inspect_on_connection", AsyncMock(return_value=snapshot(oid=99)))
    monkeypatch.setattr(adapter, "_cleanup_session", AsyncMock(return_value=None))
    ensure = AsyncMock()
    monkeypatch.setattr(adapter, "_ensure_ledger", ensure)

    with pytest.raises(MaintenanceReviewMismatch, match="changed after review"):
        await adapter.apply(plan, timeout_seconds=30, lock_timeout_seconds=5)

    ensure.assert_not_awaited()


@pytest.mark.asyncio
async def test_status_without_ledger_is_empty_and_read_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    begin = FakeCursor()
    connection = FakeConnection([begin])
    adapter = backend(connection)
    monkeypatch.setattr(adapter, "_ledger_exists", AsyncMock(return_value=False))

    result = await adapter.status(limit=10)

    assert result.operations == ()
    begin.execute.assert_awaited_once_with("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
    connection.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_reconcile_requires_an_unlocked_matching_unknown_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = reviewed_plan()
    row = record_row(plan, status=MaintenanceOperationStatus.UNKNOWN)
    connection = FakeConnection([])
    adapter = backend(connection)
    monkeypatch.setattr(adapter, "_configure_session", AsyncMock())
    monkeypatch.setattr(adapter, "_ledger_exists", AsyncMock(return_value=True))
    monkeypatch.setattr(adapter, "_validate_ledger", AsyncMock())
    monkeypatch.setattr(adapter, "_get_by_name", AsyncMock(return_value=row))
    monkeypatch.setattr(adapter, "_acquire_lock", AsyncMock(return_value=True))
    monkeypatch.setattr(adapter, "_finish_record", AsyncMock(return_value={**row, "status": MaintenanceOperationStatus.RECONCILED_SUCCEEDED.value}))
    monkeypatch.setattr(adapter, "_cleanup_session", AsyncMock(return_value=None))

    result = await adapter.reconcile(
        name=plan.name,
        review_hash=plan.review_hash,
        resolution=ReconciliationResolution.SUCCEEDED,
    )

    assert result.status is MaintenanceOperationStatus.RECONCILED_SUCCEEDED

    monkeypatch.setattr(adapter, "_acquire_lock", AsyncMock(return_value=False))
    with pytest.raises(MaintenanceBusyError, match="still active"):
        await adapter.reconcile(
            name=plan.name,
            review_hash=plan.review_hash,
            resolution=ReconciliationResolution.FAILED,
        )


@pytest.mark.asyncio
async def test_cleanup_failure_marks_pool_connection_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = reviewed_plan()
    connection = FakeConnection([])
    adapter = backend(connection)
    configure_apply(adapter, monkeypatch, plan=plan)
    monkeypatch.setattr(adapter, "_cleanup_session", AsyncMock(return_value=RuntimeError("reset failed")))
    invalid = Mock()
    monkeypatch.setattr(adapter, "_mark_connection_invalid", invalid)

    await adapter.apply(plan, timeout_seconds=30, lock_timeout_seconds=5)

    invalid.assert_called_once()


@pytest.mark.asyncio
async def test_successful_command_with_failed_status_write_is_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = reviewed_plan()
    adapter = backend(FakeConnection([]))
    _connection, cursor, finish, _unknown = configure_apply(adapter, monkeypatch, plan=plan)
    finish.side_effect = [RuntimeError("status write lost"), record_row(plan, status=MaintenanceOperationStatus.UNKNOWN)]

    with pytest.raises(MaintenanceExecutionError, match="maintenance operation failed") as error:
        await adapter.apply(plan, timeout_seconds=30, lock_timeout_seconds=5)

    cursor.execute.assert_awaited_once()
    assert error.value.outcome == "unknown"
    assert finish.await_args_list[-1].kwargs["status"] is MaintenanceOperationStatus.UNKNOWN


def test_constructor_bounds_inspection_timeout() -> None:
    with pytest.raises(ValueError, match="between 1 and 300"):
        PostgresMaintenanceBackend(cast(SqlDriver, object()), inspection_timeout_seconds=0)
