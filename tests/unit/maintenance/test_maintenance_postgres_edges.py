"""Failure-path and helper contracts for the reviewed-maintenance adapter."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from typing import cast
from unittest.mock import AsyncMock
from unittest.mock import Mock

import pytest

import postgres_mcp.maintenance.postgres as maintenance_postgres
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
from postgres_mcp.sql import DbConnPool
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
    request = MaintenanceRequest(
        name="nightly-items-maintenance",
        operation=operation,
        target=MaintenanceTarget("app", "items"),
    )
    return MaintenancePlanner().create_plan(request, target_snapshot)


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
    monkeypatch.setattr(adapter, "_ledger_exists", AsyncMock(return_value=existing is not None))
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
    monkeypatch.setattr(adapter, "_ensure_ledger", AsyncMock())
    monkeypatch.setattr(adapter, "_validate_ledger", AsyncMock())
    monkeypatch.setattr(adapter, "_get_by_name", AsyncMock(return_value=existing))
    monkeypatch.setattr(adapter, "_insert_running_record", AsyncMock(return_value=record_row(plan)))
    monkeypatch.setattr(adapter, "_restart_record", AsyncMock(return_value=record_row(plan)))
    monkeypatch.setattr(adapter, "_finish_record", finish)
    monkeypatch.setattr(adapter, "_best_effort_finish_unknown", unknown)
    monkeypatch.setattr(adapter, "_cleanup_session", AsyncMock(return_value=None))
    return connection, cursor, finish, unknown


class ImmediateTimeout:
    async def __aenter__(self) -> None:
        raise TimeoutError

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        return None


class SqlStateError(RuntimeError):
    sqlstate = "42501"


def configure_reconcile(
    adapter: PostgresMaintenanceBackend,
    monkeypatch: pytest.MonkeyPatch,
    *,
    row: dict[str, Any] | None,
    ledger_exists: bool = True,
    lock_acquired: bool = True,
    cleanup_error: BaseException | None = None,
) -> tuple[AsyncMock, Mock]:
    finish = AsyncMock(return_value={**row, "status": MaintenanceOperationStatus.RECONCILED_SUCCEEDED.value} if row else None)
    invalid = Mock()
    monkeypatch.setattr(adapter, "_configure_session", AsyncMock())
    monkeypatch.setattr(adapter, "_ledger_exists", AsyncMock(return_value=ledger_exists))
    monkeypatch.setattr(adapter, "_validate_ledger", AsyncMock())
    monkeypatch.setattr(adapter, "_get_by_name", AsyncMock(return_value=row))
    monkeypatch.setattr(adapter, "_acquire_lock", AsyncMock(return_value=lock_acquired))
    monkeypatch.setattr(adapter, "_finish_record", finish)
    monkeypatch.setattr(adapter, "_cleanup_session", AsyncMock(return_value=cleanup_error))
    monkeypatch.setattr(adapter, "_mark_connection_invalid", invalid)
    return finish, invalid


def test_constructor_rejects_noninteger_inspection_timeout() -> None:
    with pytest.raises(ValueError, match="must be an integer"):
        PostgresMaintenanceBackend(cast(SqlDriver, object()), inspection_timeout_seconds=cast(Any, 1.5))


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["inspect", "apply", "status", "reconcile"])
async def test_public_operations_report_stable_timeouts(
    monkeypatch: pytest.MonkeyPatch,
    method: str,
) -> None:
    monkeypatch.setattr(maintenance_postgres.asyncio, "timeout", lambda _seconds: ImmediateTimeout())
    adapter = backend(FakeConnection([]))
    plan = reviewed_plan()

    with pytest.raises(MaintenanceExecutionError, match="timed out") as error:
        if method == "inspect":
            await adapter.inspect(MaintenanceRequest(plan.name, plan.operation, plan.target, plan.options))
        elif method == "apply":
            await adapter.apply(plan, timeout_seconds=30, lock_timeout_seconds=5)
        elif method == "status":
            await adapter.status(limit=10)
        else:
            await adapter.reconcile(
                name=plan.name,
                review_hash=plan.review_hash,
                resolution=ReconciliationResolution.SUCCEEDED,
            )

    assert error.value.phase in {"inspect", "prepare", "status", "reconcile"}


@pytest.mark.asyncio
async def test_apply_propagates_domain_execution_error_without_wrapping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = reviewed_plan()
    adapter = backend(FakeConnection([]))
    configure_apply(adapter, monkeypatch, plan=plan)
    expected = MaintenanceExecutionError("already classified", phase="prepare", outcome="not_started")
    monkeypatch.setattr(adapter, "_inspect_on_connection", AsyncMock(side_effect=expected))

    with pytest.raises(MaintenanceExecutionError) as error:
        await adapter.apply(plan, timeout_seconds=30, lock_timeout_seconds=5)

    assert error.value is expected


@pytest.mark.asyncio
async def test_apply_restarts_a_failed_record_before_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = reviewed_plan()
    adapter = backend(FakeConnection([]))
    configure_apply(
        adapter,
        monkeypatch,
        plan=plan,
        existing=record_row(plan, status=MaintenanceOperationStatus.FAILED),
    )
    restart = AsyncMock(return_value=record_row(plan))
    monkeypatch.setattr(adapter, "_restart_record", restart)

    result = await adapter.apply(plan, timeout_seconds=30, lock_timeout_seconds=5)

    assert result.status is MaintenanceOperationStatus.SUCCEEDED
    restart.assert_awaited_once()


@pytest.mark.asyncio
async def test_apply_reports_unknown_when_failure_status_cannot_be_persisted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = reviewed_plan()
    adapter = backend(FakeConnection([]))
    _connection, _cursor, finish, _unknown = configure_apply(
        adapter,
        monkeypatch,
        plan=plan,
        execute_error=RuntimeError("command failed"),
    )
    finish.side_effect = RuntimeError("status write failed")

    with pytest.raises(MaintenanceExecutionError) as error:
        await adapter.apply(plan, timeout_seconds=30, lock_timeout_seconds=5)

    assert error.value.outcome == "unknown"


@pytest.mark.asyncio
async def test_reconcile_rejects_missing_ledger_record_or_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = reviewed_plan()

    adapter = backend(FakeConnection([]))
    configure_reconcile(adapter, monkeypatch, row=None, ledger_exists=False)
    with pytest.raises(MaintenanceConflictError, match="ledger does not exist"):
        await adapter.reconcile(
            name=plan.name,
            review_hash=plan.review_hash,
            resolution=ReconciliationResolution.SUCCEEDED,
        )

    adapter = backend(FakeConnection([]))
    configure_reconcile(adapter, monkeypatch, row=None)
    with pytest.raises(MaintenanceConflictError, match="does not exist"):
        await adapter.reconcile(
            name=plan.name,
            review_hash=plan.review_hash,
            resolution=ReconciliationResolution.SUCCEEDED,
        )

    row = record_row(plan, status=MaintenanceOperationStatus.UNKNOWN)
    adapter = backend(FakeConnection([]))
    configure_reconcile(adapter, monkeypatch, row=row)
    with pytest.raises(MaintenanceReviewMismatch, match="does not match"):
        await adapter.reconcile(
            name=plan.name,
            review_hash="0" * 64,
            resolution=ReconciliationResolution.SUCCEEDED,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "resolution", "expected_status", "error"),
    [
        (
            MaintenanceOperationStatus.SUCCEEDED,
            ReconciliationResolution.SUCCEEDED,
            MaintenanceOperationStatus.ALREADY_SUCCEEDED,
            None,
        ),
        (
            MaintenanceOperationStatus.SUCCEEDED,
            ReconciliationResolution.FAILED,
            None,
            "successful operation cannot be reconciled as failed",
        ),
        (
            MaintenanceOperationStatus.FAILED,
            ReconciliationResolution.FAILED,
            MaintenanceOperationStatus.FAILED,
            None,
        ),
        (
            MaintenanceOperationStatus.RECONCILED_FAILED,
            ReconciliationResolution.SUCCEEDED,
            None,
            "failed operation cannot be reconciled as succeeded",
        ),
    ],
)
async def test_reconcile_terminal_state_matrix(
    monkeypatch: pytest.MonkeyPatch,
    status: MaintenanceOperationStatus,
    resolution: ReconciliationResolution,
    expected_status: MaintenanceOperationStatus | None,
    error: str | None,
) -> None:
    plan = reviewed_plan()
    row = record_row(plan, status=status)
    adapter = backend(FakeConnection([]))
    configure_reconcile(adapter, monkeypatch, row=row)

    if error:
        with pytest.raises(MaintenanceConflictError, match=error):
            await adapter.reconcile(name=plan.name, review_hash=plan.review_hash, resolution=resolution)
    else:
        result = await adapter.reconcile(name=plan.name, review_hash=plan.review_hash, resolution=resolution)
        assert result.status is expected_status


@pytest.mark.asyncio
async def test_reconcile_cleanup_failure_invalidates_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = reviewed_plan()
    row = record_row(plan, status=MaintenanceOperationStatus.UNKNOWN)
    adapter = backend(FakeConnection([]))
    _finish, invalid = configure_reconcile(
        adapter,
        monkeypatch,
        row=row,
        cleanup_error=RuntimeError("reset failed"),
    )

    await adapter.reconcile(
        name=plan.name,
        review_hash=plan.review_hash,
        resolution=ReconciliationResolution.FAILED,
    )

    invalid.assert_called_once()


def test_analyze_skip_locked_command_is_structured() -> None:
    plan = MaintenancePlanner().create_plan(
        MaintenanceRequest(
            name="analyze-items",
            operation=MaintenanceOperation.ANALYZE,
            target=MaintenanceTarget("app", "items"),
            options=MaintenanceOptions(skip_locked=True),
        ),
        snapshot(),
    )
    assert PostgresMaintenanceBackend._build_command(plan).as_string(None) == 'ANALYZE (SKIP_LOCKED) "app"."items"'


@pytest.mark.asyncio
async def test_lock_and_ledger_boolean_helpers_accept_mapping_rows() -> None:
    adapter = backend(FakeConnection([FakeCursor(one={"locked": True})]))
    assert await adapter._acquire_lock(cast(Any, adapter.sql_driver)._connection, 42) is True

    adapter = backend(FakeConnection([FakeCursor(one={"exists": True})]))
    assert await adapter._ledger_exists(cast(Any, adapter.sql_driver)._connection) is True


@pytest.mark.asyncio
async def test_cleanup_returns_reset_error() -> None:
    failure = RuntimeError("reset failed")
    connection = FakeConnection([FakeCursor(execute_error=failure)], autocommit=True)
    result = await backend(connection)._cleanup_session(
        connection,
        target_oid=42,
        lock_acquired=False,
        original_autocommit=True,
    )
    assert result is failure


@pytest.mark.asyncio
async def test_trusted_ledger_rejects_relation_and_column_spoofing() -> None:
    adapter = backend(FakeConnection([]))
    bad_relation = FakeConnection([FakeCursor(one={"oid": 42, "relkind": "v"})])
    with pytest.raises(MaintenanceConflictError, match="trusted ledger contract"):
        await adapter._validate_ledger(bad_relation)

    relation = {
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
    bad_columns = FakeConnection([FakeCursor(one=relation), FakeCursor(rows=[])])
    with pytest.raises(MaintenanceConflictError, match="trusted ledger contract"):
        await adapter._validate_ledger(bad_columns)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "message"),
    [
        ("_insert_running_record", "insert did not return"),
        ("_restart_record", "restart did not return"),
        ("_finish_record", "finalization did not return"),
    ],
)
async def test_ledger_writes_require_returned_rows(method: str, message: str) -> None:
    plan = reviewed_plan()
    adapter = backend(FakeConnection([FakeCursor(one=None)]))
    connection = cast(Any, adapter.sql_driver)._connection
    with pytest.raises(RuntimeError, match=message):
        if method == "_insert_running_record":
            await adapter._insert_running_record(connection, plan)
        elif method == "_restart_record":
            await adapter._restart_record(connection, plan)
        else:
            await adapter._finish_record(
                connection,
                name=plan.name,
                status=MaintenanceOperationStatus.SUCCEEDED,
                error_code=None,
            )


@pytest.mark.asyncio
async def test_best_effort_unknown_swallows_secondary_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = backend(FakeConnection([]))
    finish = AsyncMock(side_effect=RuntimeError("unavailable"))
    monkeypatch.setattr(adapter, "_finish_record", finish)

    await adapter._best_effort_finish_unknown(FakeConnection([]), "name", "cancelled")

    finish.assert_awaited_once()


def test_corrupt_plan_pool_invalidation_error_codes_and_rollback_helpers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(MaintenanceConflictError, match="corrupted reviewed plan"):
        PostgresMaintenanceBackend._verified_plan({})

    pool = DbConnPool()
    invalid = Mock()
    monkeypatch.setattr(pool, "mark_invalid", invalid)
    adapter = PostgresMaintenanceBackend(SqlDriver(conn=pool))
    failure = RuntimeError("connection lost")
    adapter._mark_connection_invalid(failure)
    invalid.assert_called_once_with(failure)

    assert maintenance_postgres._error_code(SqlStateError("denied")) == "42501"


@pytest.mark.asyncio
async def test_attempt_rollback_reports_failure() -> None:
    connection = FakeConnection([])
    connection.rollback.side_effect = RuntimeError("connection lost")
    assert await maintenance_postgres._attempt_rollback(connection) is False
