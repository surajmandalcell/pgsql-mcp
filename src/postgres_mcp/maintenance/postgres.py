"""PostgreSQL adapter for reviewed nontransactional maintenance."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Mapping
from typing import Any

from psycopg.rows import dict_row
from psycopg.sql import SQL
from psycopg.sql import Composable
from psycopg.sql import Identifier
from psycopg.sql import Literal
from psycopg.types.json import Jsonb

from postgres_mcp.sql import DbConnPool
from postgres_mcp.sql import SqlDriver

from .domain import MaintenanceBusyError
from .domain import MaintenanceConflictError
from .domain import MaintenanceExecutionError
from .domain import MaintenanceOperation
from .domain import MaintenanceOperationResult
from .domain import MaintenanceOperationStatus
from .domain import MaintenancePlan
from .domain import MaintenanceRecord
from .domain import MaintenanceRequest
from .domain import MaintenanceReviewMismatch
from .domain import MaintenanceStatusSnapshot
from .domain import MaintenanceTarget
from .domain import MaintenanceValidationError
from .domain import ReconciliationResolution
from .domain import TargetSnapshot

LEDGER_TABLE_NAME = "_postgres_mcp_maintenance"
_LEDGER_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ADVISORY_LOCK_NAMESPACE = "pgsql-mcp:reviewed-maintenance:v1"
_TERMINAL_SUCCESS = {
    MaintenanceOperationStatus.SUCCEEDED,
    MaintenanceOperationStatus.RECONCILED_SUCCEEDED,
}
_TERMINAL_FAILURE = {
    MaintenanceOperationStatus.FAILED,
    MaintenanceOperationStatus.RECONCILED_FAILED,
}
_EXPECTED_COLUMNS: tuple[tuple[str, int, bool, str], ...] = (
    ("id", 20, True, "a"),
    ("name", 25, True, ""),
    ("review_hash", 25, True, ""),
    ("plan_version", 23, True, ""),
    ("operation", 25, True, ""),
    ("target_schema", 19, True, ""),
    ("target_name", 19, True, ""),
    ("target_oid", 26, True, ""),
    ("plan", 3802, True, ""),
    ("status", 25, True, ""),
    ("started_at", 1184, True, ""),
    ("finished_at", 1184, False, ""),
    ("error_code", 25, False, ""),
    ("applied_by", 19, True, ""),
)


class PostgresMaintenanceBackend:
    """Execute reviewed maintenance with durable nontransactional status."""

    def __init__(
        self,
        sql_driver: SqlDriver,
        *,
        ledger_schema: str = "public",
        ledger_table: str = LEDGER_TABLE_NAME,
        inspection_timeout_seconds: int = 30,
    ) -> None:
        self.sql_driver = sql_driver
        self.ledger_schema = _validated_identifier(ledger_schema, "ledger_schema")
        self.ledger_table = _validated_identifier(ledger_table, "ledger_table")
        self.ledger_regclass = f"{self.ledger_schema}.{self.ledger_table}"
        self.inspection_timeout_seconds = inspection_timeout_seconds
        self._ledger = SQL("{}.{}").format(
            Identifier(self.ledger_schema),
            Identifier(self.ledger_table),
        )

    async def inspect(self, request: MaintenanceRequest) -> TargetSnapshot:
        try:
            async with asyncio.timeout(self.inspection_timeout_seconds):
                async with self.sql_driver.connection() as connection:
                    try:
                        return await self._inspect_on_connection(connection, request.target)
                    finally:
                        await _attempt_rollback(connection)
        except TimeoutError as exc:
            raise MaintenanceExecutionError(
                "maintenance target inspection timed out",
                phase="inspect",
                outcome="not_started",
            ) from exc

    async def apply(
        self,
        plan: MaintenancePlan,
        *,
        timeout_seconds: int,
        lock_timeout_seconds: int,
    ) -> MaintenanceOperationResult:
        plan.assert_integrity()
        operation_started = False
        record_started = False
        try:
            async with asyncio.timeout(timeout_seconds):
                async with self.sql_driver.connection() as connection:
                    original_autocommit = bool(connection.autocommit)
                    lock_acquired = False
                    active_row: dict[str, Any] | None = None
                    try:
                        if not original_autocommit:
                            await connection.set_autocommit(True)
                        await self._configure_session(
                            connection,
                            timeout_seconds=timeout_seconds,
                            lock_timeout_seconds=lock_timeout_seconds,
                        )
                        lock_acquired = await self._acquire_lock(connection, plan.target_oid)
                        if not lock_acquired:
                            raise MaintenanceBusyError("another maintenance operation owns the target lock")

                        snapshot = await self._inspect_on_connection(connection, plan.target)
                        self._verify_snapshot(plan, snapshot)
                        await self._ensure_ledger(connection)
                        await self._validate_ledger(connection)
                        existing = await self._get_by_name(connection, plan.name)
                        if existing is not None:
                            stored_plan = self._verified_plan(existing)
                            if stored_plan.review_hash != plan.review_hash:
                                raise MaintenanceConflictError(
                                    f"maintenance operation {plan.name!r} already exists with different reviewed content"
                                )
                            existing_status = MaintenanceOperationStatus(str(existing["status"]))
                            if existing_status in _TERMINAL_SUCCESS:
                                return MaintenanceOperationResult(
                                    MaintenanceOperationStatus.ALREADY_SUCCEEDED,
                                    _record_from_row(existing),
                                )
                            if existing_status in {
                                MaintenanceOperationStatus.RUNNING,
                                MaintenanceOperationStatus.UNKNOWN,
                            }:
                                raise MaintenanceConflictError(
                                    f"maintenance operation {plan.name!r} has an unresolved outcome"
                                )
                            active_row = await self._restart_record(connection, plan)
                        else:
                            active_row = await self._insert_running_record(connection, plan)
                        record_started = True

                        operation_started = True
                        async with connection.cursor() as cursor:
                            await cursor.execute(self._build_command(plan))
                        active_row = await self._finish_record(
                            connection,
                            name=plan.name,
                            status=MaintenanceOperationStatus.SUCCEEDED,
                            error_code=None,
                        )
                        return MaintenanceOperationResult(
                            MaintenanceOperationStatus.SUCCEEDED,
                            _record_from_row(active_row),
                        )
                    except MaintenanceExecutionError:
                        raise
                    except (MaintenanceBusyError, MaintenanceConflictError, MaintenanceReviewMismatch):
                        raise
                    except asyncio.CancelledError:
                        if record_started:
                            await self._best_effort_finish_unknown(connection, plan.name, "cancelled")
                        raise
                    except BaseException as exc:
                        error_code = _error_code(exc)
                        outcome = "failed"
                        if record_started:
                            try:
                                await self._finish_record(
                                    connection,
                                    name=plan.name,
                                    status=MaintenanceOperationStatus.FAILED,
                                    error_code=error_code,
                                )
                            except BaseException:
                                outcome = "unknown"
                        raise MaintenanceExecutionError(
                            "maintenance operation failed",
                            phase="execute" if operation_started else "prepare",
                            outcome=outcome,
                            error_code=error_code,
                        ) from exc
                    finally:
                        cleanup_error = await self._cleanup_session(
                            connection,
                            target_oid=plan.target_oid,
                            lock_acquired=lock_acquired,
                            original_autocommit=original_autocommit,
                        )
                        if cleanup_error is not None:
                            self._mark_connection_invalid(cleanup_error)
        except TimeoutError as exc:
            raise MaintenanceExecutionError(
                "maintenance operation timed out; reconcile the target before retrying",
                phase="execute" if operation_started else "prepare",
                outcome="unknown" if record_started else "not_started",
                error_code="timeout",
            ) from exc

    async def status(self, *, limit: int) -> MaintenanceStatusSnapshot:
        try:
            async with asyncio.timeout(self.inspection_timeout_seconds):
                async with self.sql_driver.connection() as connection:
                    try:
                        async with connection.cursor() as cursor:
                            await cursor.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
                        if not await self._ledger_exists(connection):
                            return MaintenanceStatusSnapshot(())
                        await self._validate_ledger(connection)
                        async with connection.cursor(row_factory=dict_row) as cursor:
                            await cursor.execute(
                                SQL(
                                    """
                                    SELECT
                                        id AS operation_id,
                                        name,
                                        review_hash,
                                        plan_version,
                                        operation,
                                        target_schema,
                                        target_name,
                                        target_oid::bigint AS target_oid,
                                        status,
                                        started_at,
                                        finished_at,
                                        error_code,
                                        applied_by
                                    FROM {}
                                    ORDER BY id DESC
                                    LIMIT %s
                                    """
                                ).format(self._ledger),
                                [limit],
                            )
                            rows = await cursor.fetchall()
                        return MaintenanceStatusSnapshot(
                            tuple(_record_from_row(_mapping(row)) for row in rows)
                        )
                    finally:
                        await _attempt_rollback(connection)
        except TimeoutError as exc:
            raise MaintenanceExecutionError(
                "maintenance status inspection timed out",
                phase="status",
                outcome="not_started",
            ) from exc

    async def reconcile(
        self,
        *,
        name: str,
        review_hash: str,
        resolution: ReconciliationResolution,
    ) -> MaintenanceOperationResult:
        try:
            async with asyncio.timeout(self.inspection_timeout_seconds):
                async with self.sql_driver.connection() as connection:
                    original_autocommit = bool(connection.autocommit)
                    lock_acquired = False
                    target_oid = 0
                    try:
                        if not original_autocommit:
                            await connection.set_autocommit(True)
                        await self._configure_session(
                            connection,
                            timeout_seconds=self.inspection_timeout_seconds,
                            lock_timeout_seconds=5,
                        )
                        if not await self._ledger_exists(connection):
                            raise MaintenanceConflictError("maintenance ledger does not exist")
                        await self._validate_ledger(connection)
                        row = await self._get_by_name(connection, name)
                        if row is None:
                            raise MaintenanceConflictError(f"maintenance operation {name!r} does not exist")
                        stored_plan = self._verified_plan(row)
                        if stored_plan.review_hash != review_hash:
                            raise MaintenanceReviewMismatch(
                                "supplied review_hash does not match the stored maintenance plan"
                            )
                        target_oid = stored_plan.target_oid
                        lock_acquired = await self._acquire_lock(connection, target_oid)
                        if not lock_acquired:
                            raise MaintenanceBusyError("maintenance operation is still active")
                        current = MaintenanceOperationStatus(str(row["status"]))
                        if current in _TERMINAL_SUCCESS:
                            if resolution is ReconciliationResolution.SUCCEEDED:
                                return MaintenanceOperationResult(
                                    MaintenanceOperationStatus.ALREADY_SUCCEEDED,
                                    _record_from_row(row),
                                )
                            raise MaintenanceConflictError("a successful operation cannot be reconciled as failed")
                        if current in _TERMINAL_FAILURE:
                            if resolution is ReconciliationResolution.FAILED:
                                return MaintenanceOperationResult(
                                    MaintenanceOperationStatus.FAILED,
                                    _record_from_row(row),
                                )
                            raise MaintenanceConflictError("a failed operation cannot be reconciled as succeeded")
                        resolved_status = (
                            MaintenanceOperationStatus.RECONCILED_SUCCEEDED
                            if resolution is ReconciliationResolution.SUCCEEDED
                            else MaintenanceOperationStatus.RECONCILED_FAILED
                        )
                        updated = await self._finish_record(
                            connection,
                            name=name,
                            status=resolved_status,
                            error_code="operator_reconciled",
                        )
                        return MaintenanceOperationResult(resolved_status, _record_from_row(updated))
                    finally:
                        cleanup_error = await self._cleanup_session(
                            connection,
                            target_oid=target_oid,
                            lock_acquired=lock_acquired,
                            original_autocommit=original_autocommit,
                        )
                        if cleanup_error is not None:
                            self._mark_connection_invalid(cleanup_error)
        except TimeoutError as exc:
            raise MaintenanceExecutionError(
                "maintenance reconciliation timed out",
                phase="reconcile",
                outcome="unknown",
                error_code="timeout",
            ) from exc

    async def _inspect_on_connection(
        self,
        connection: Any,
        target: MaintenanceTarget,
    ) -> TargetSnapshot:
        async with connection.cursor(row_factory=dict_row) as cursor:
            await cursor.execute(
                """
                SELECT
                    c.oid::bigint AS oid,
                    c.relkind::text AS relation_kind,
                    c.relpersistence::text AS persistence,
                    c.relispartition,
                    c.relispopulated,
                    COALESCE(ix.indisexclusion, false) AS is_exclusion_index,
                    EXISTS (
                        SELECT 1
                        FROM pg_catalog.pg_index unique_index
                        WHERE unique_index.indrelid = c.oid
                          AND unique_index.indisunique
                          AND unique_index.indisvalid
                          AND unique_index.indisready
                          AND unique_index.indpred IS NULL
                          AND unique_index.indexprs IS NULL
                    ) AS has_usable_unique_index
                FROM pg_catalog.pg_class c
                JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
                LEFT JOIN pg_catalog.pg_index ix ON ix.indexrelid = c.oid
                WHERE n.nspname = %s AND c.relname = %s
                """,
                [target.schema, target.name],
            )
            raw = await cursor.fetchone()
        row = _mapping(raw)
        if not row:
            raise MaintenanceValidationError(f"maintenance target {target.schema}.{target.name} does not exist")
        return TargetSnapshot(
            oid=int(row["oid"]),
            relation_kind=str(row["relation_kind"]),
            persistence=str(row["persistence"]),
            is_partition=bool(row["relispartition"]),
            is_populated=bool(row["relispopulated"]),
            has_usable_unique_index=bool(row["has_usable_unique_index"]),
            is_exclusion_index=bool(row["is_exclusion_index"]),
        )

    @staticmethod
    def _verify_snapshot(plan: MaintenancePlan, snapshot: TargetSnapshot) -> None:
        expected = (
            plan.target_oid,
            plan.target_kind,
            plan.target_persistence,
            plan.is_partition,
            bool(plan.preconditions.get("is_populated")),
            bool(plan.preconditions.get("has_usable_unique_index")),
            bool(plan.preconditions.get("is_exclusion_index")),
        )
        actual = (
            snapshot.oid,
            snapshot.relation_kind,
            snapshot.persistence,
            snapshot.is_partition,
            snapshot.is_populated,
            snapshot.has_usable_unique_index,
            snapshot.is_exclusion_index,
        )
        if actual != expected:
            raise MaintenanceReviewMismatch("maintenance target changed after review")

    @staticmethod
    def _build_command(plan: MaintenancePlan) -> Composable:
        relation = SQL("{}.{}").format(
            Identifier(plan.target.schema),
            Identifier(plan.target.name),
        )
        if plan.operation is MaintenanceOperation.VACUUM_ANALYZE:
            options: list[Composable] = [SQL("ANALYZE")]
            if plan.options.skip_locked:
                options.append(SQL("SKIP_LOCKED"))
            if plan.options.index_cleanup != "auto":
                options.append(
                    SQL("INDEX_CLEANUP {}").format(SQL(plan.options.index_cleanup.upper()))
                )
            if plan.options.parallel:
                options.append(SQL("PARALLEL {}").format(Literal(plan.options.parallel)))
            return SQL("VACUUM ({}) {}").format(SQL(", ").join(options), relation)
        if plan.operation is MaintenanceOperation.ANALYZE:
            if plan.options.skip_locked:
                return SQL("ANALYZE (SKIP_LOCKED) {}").format(relation)
            return SQL("ANALYZE {}").format(relation)
        if plan.operation is MaintenanceOperation.REINDEX_INDEX_CONCURRENTLY:
            return SQL("REINDEX INDEX CONCURRENTLY {}").format(relation)
        return SQL("REFRESH MATERIALIZED VIEW CONCURRENTLY {}").format(relation)

    async def _configure_session(
        self,
        connection: Any,
        *,
        timeout_seconds: int,
        lock_timeout_seconds: int,
    ) -> None:
        async with connection.cursor() as cursor:
            await cursor.execute(
                "SELECT set_config('statement_timeout', %s, false)",
                [f"{max(1, timeout_seconds * 1000)}ms"],
            )
            await cursor.execute(
                "SELECT set_config('lock_timeout', %s, false)",
                [f"{max(1, lock_timeout_seconds * 1000)}ms"],
            )
            await cursor.execute("SELECT set_config('row_security', 'on', false)")
            await cursor.execute("SELECT set_config('search_path', 'pg_catalog', false)")
            await cursor.execute(
                "SELECT set_config('application_name', 'pgsql-mcp:reviewed-maintenance', false)"
            )

    async def _acquire_lock(self, connection: Any, target_oid: int) -> bool:
        async with connection.cursor() as cursor:
            await cursor.execute(
                "SELECT pg_catalog.pg_try_advisory_lock(pg_catalog.hashtextextended(%s, 0))",
                [self._lock_name(target_oid)],
            )
            row = await cursor.fetchone()
        if isinstance(row, Mapping):
            return bool(next(iter(row.values()), False))
        return bool(row and row[0])

    async def _cleanup_session(
        self,
        connection: Any,
        *,
        target_oid: int,
        lock_acquired: bool,
        original_autocommit: bool,
    ) -> BaseException | None:
        try:
            if lock_acquired:
                async with connection.cursor() as cursor:
                    await cursor.execute(
                        "SELECT pg_catalog.pg_advisory_unlock(pg_catalog.hashtextextended(%s, 0))",
                        [self._lock_name(target_oid)],
                    )
            async with connection.cursor() as cursor:
                for setting in (
                    "statement_timeout",
                    "lock_timeout",
                    "row_security",
                    "search_path",
                    "application_name",
                ):
                    await cursor.execute(SQL("RESET {}").format(Identifier(setting)))
            if bool(connection.autocommit) != original_autocommit:
                await connection.set_autocommit(original_autocommit)
        except BaseException as exc:
            return exc
        return None

    def _lock_name(self, target_oid: int) -> str:
        return f"{_ADVISORY_LOCK_NAMESPACE}:{self.ledger_regclass}:{target_oid}"

    async def _ensure_ledger(self, connection: Any) -> None:
        async with connection.cursor() as cursor:
            await cursor.execute(SQL("CREATE SCHEMA IF NOT EXISTS {}").format(Identifier(self.ledger_schema)))
            await cursor.execute(
                SQL(
                    """
                    CREATE TABLE IF NOT EXISTS {} (
                        id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                        name text NOT NULL UNIQUE,
                        review_hash text NOT NULL,
                        plan_version integer NOT NULL,
                        operation text NOT NULL,
                        target_schema name NOT NULL,
                        target_name name NOT NULL,
                        target_oid oid NOT NULL,
                        plan jsonb NOT NULL,
                        status text NOT NULL,
                        started_at timestamp with time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        finished_at timestamp with time zone,
                        error_code text,
                        applied_by name NOT NULL DEFAULT CURRENT_USER
                    )
                    """
                ).format(self._ledger)
            )
            await cursor.execute(
                SQL("CREATE INDEX IF NOT EXISTS {} ON {} (status, id)").format(
                    Identifier(f"{self.ledger_table}_status_idx"),
                    self._ledger,
                )
            )

    async def _ledger_exists(self, connection: Any) -> bool:
        async with connection.cursor() as cursor:
            await cursor.execute("SELECT pg_catalog.to_regclass(%s) IS NOT NULL", [self.ledger_regclass])
            row = await cursor.fetchone()
        if isinstance(row, Mapping):
            return bool(next(iter(row.values()), False))
        return bool(row and row[0])

    async def _validate_ledger(self, connection: Any) -> None:
        async with connection.cursor(row_factory=dict_row) as cursor:
            await cursor.execute(
                """
                SELECT
                    c.oid,
                    c.relkind::text AS relkind,
                    c.relpersistence::text AS relpersistence,
                    c.relispartition,
                    c.relrowsecurity,
                    c.relforcerowsecurity,
                    c.relowner = (SELECT oid FROM pg_catalog.pg_roles WHERE rolname = CURRENT_USER) AS owned_by_current_user,
                    (SELECT count(*)::integer FROM pg_catalog.pg_trigger t WHERE t.tgrelid = c.oid AND NOT t.tgisinternal) AS trigger_count,
                    (SELECT count(*)::integer FROM pg_catalog.pg_rewrite r WHERE r.ev_class = c.oid AND r.rulename <> '_RETURN') AS rule_count
                FROM pg_catalog.pg_class c
                JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = %s AND c.relname = %s
                """,
                [self.ledger_schema, self.ledger_table],
            )
            relation_raw = await cursor.fetchone()
        relation = _mapping(relation_raw)
        trusted = bool(relation) and all(
            (
                relation.get("relkind") == "r",
                relation.get("relpersistence") == "p",
                relation.get("relispartition") is False,
                relation.get("relrowsecurity") is False,
                relation.get("relforcerowsecurity") is False,
                relation.get("owned_by_current_user") is True,
                int(relation.get("trigger_count", -1)) == 0,
                int(relation.get("rule_count", -1)) == 0,
            )
        )
        if not trusted:
            raise MaintenanceConflictError("maintenance ledger does not satisfy the trusted ledger contract")

        async with connection.cursor(row_factory=dict_row) as cursor:
            await cursor.execute(
                """
                SELECT
                    a.attname AS name,
                    a.atttypid::integer AS type_oid,
                    a.attnotnull AS not_null,
                    a.attidentity::text AS identity_kind
                FROM pg_catalog.pg_attribute a
                WHERE a.attrelid = %s
                  AND a.attnum > 0
                  AND NOT a.attisdropped
                ORDER BY a.attnum
                """,
                [relation["oid"]],
            )
            column_rows = await cursor.fetchall()
        actual = tuple(
            (
                str(_mapping(row).get("name")),
                int(_mapping(row).get("type_oid", -1)),
                bool(_mapping(row).get("not_null")),
                str(_mapping(row).get("identity_kind") or ""),
            )
            for row in column_rows
        )
        if actual != _EXPECTED_COLUMNS:
            raise MaintenanceConflictError("maintenance ledger does not satisfy the trusted ledger contract")

    async def _get_by_name(self, connection: Any, name: str) -> dict[str, Any] | None:
        async with connection.cursor(row_factory=dict_row) as cursor:
            await cursor.execute(
                SQL("SELECT * FROM {} WHERE name = %s").format(self._ledger),
                [name],
            )
            row = await cursor.fetchone()
        return _mapping(row) if row is not None else None

    async def _insert_running_record(
        self,
        connection: Any,
        plan: MaintenancePlan,
    ) -> dict[str, Any]:
        async with connection.cursor(row_factory=dict_row) as cursor:
            await cursor.execute(
                SQL(
                    """
                    INSERT INTO {} (
                        name, review_hash, plan_version, operation,
                        target_schema, target_name, target_oid, plan, status
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING *
                    """
                ).format(self._ledger),
                [
                    plan.name,
                    plan.review_hash,
                    plan.plan_version,
                    plan.operation.value,
                    plan.target.schema,
                    plan.target.name,
                    plan.target_oid,
                    Jsonb(plan.canonical_payload()),
                    MaintenanceOperationStatus.RUNNING.value,
                ],
            )
            row = await cursor.fetchone()
        if row is None:
            raise RuntimeError("maintenance ledger insert did not return a row")
        return _mapping(row)

    async def _restart_record(
        self,
        connection: Any,
        plan: MaintenancePlan,
    ) -> dict[str, Any]:
        async with connection.cursor(row_factory=dict_row) as cursor:
            await cursor.execute(
                SQL(
                    """
                    UPDATE {}
                    SET status = %s,
                        started_at = CURRENT_TIMESTAMP,
                        finished_at = NULL,
                        error_code = NULL,
                        applied_by = CURRENT_USER
                    WHERE name = %s
                    RETURNING *
                    """
                ).format(self._ledger),
                [MaintenanceOperationStatus.RUNNING.value, plan.name],
            )
            row = await cursor.fetchone()
        if row is None:
            raise RuntimeError("maintenance ledger restart did not return a row")
        return _mapping(row)

    async def _finish_record(
        self,
        connection: Any,
        *,
        name: str,
        status: MaintenanceOperationStatus,
        error_code: str | None,
    ) -> dict[str, Any]:
        async with connection.cursor(row_factory=dict_row) as cursor:
            await cursor.execute(
                SQL(
                    """
                    UPDATE {}
                    SET status = %s,
                        finished_at = CURRENT_TIMESTAMP,
                        error_code = %s
                    WHERE name = %s
                    RETURNING *
                    """
                ).format(self._ledger),
                [status.value, error_code, name],
            )
            row = await cursor.fetchone()
        if row is None:
            raise RuntimeError("maintenance ledger finalization did not return a row")
        return _mapping(row)

    async def _best_effort_finish_unknown(
        self,
        connection: Any,
        name: str,
        error_code: str,
    ) -> None:
        try:
            await self._finish_record(
                connection,
                name=name,
                status=MaintenanceOperationStatus.UNKNOWN,
                error_code=error_code,
            )
        except BaseException:
            return

    @staticmethod
    def _verified_plan(row: Mapping[str, Any]) -> MaintenancePlan:
        try:
            return MaintenancePlan.from_canonical_payload(
                row["plan"],
                review_hash=str(row["review_hash"]),
            )
        except (MaintenanceValidationError, KeyError, TypeError, ValueError) as exc:
            raise MaintenanceConflictError("maintenance ledger contains a corrupted reviewed plan") from exc

    def _mark_connection_invalid(self, error: BaseException) -> None:
        if isinstance(self.sql_driver.conn, DbConnPool):
            self.sql_driver.conn.mark_invalid(error)


def _validated_identifier(value: str, label: str) -> str:
    normalized = value.strip()
    if not _LEDGER_IDENTIFIER.fullmatch(normalized):
        raise ValueError(f"{label} must be one unquoted PostgreSQL identifier")
    return normalized


def _mapping(row: Any) -> dict[str, Any]:
    if isinstance(row, Mapping):
        return dict(row)
    return {}


def _record_from_row(row: Mapping[str, Any]) -> MaintenanceRecord:
    return MaintenanceRecord(
        operation_id=int(row.get("operation_id", row["id"])),
        name=str(row["name"]),
        review_hash=str(row["review_hash"]),
        plan_version=int(row["plan_version"]),
        operation=MaintenanceOperation(str(row["operation"])),
        target=MaintenanceTarget(str(row["target_schema"]), str(row["target_name"])),
        target_oid=int(row["target_oid"]),
        status=MaintenanceOperationStatus(str(row["status"])),
        started_at=row["started_at"],
        finished_at=row.get("finished_at"),
        error_code=str(row["error_code"]) if row.get("error_code") is not None else None,
        applied_by=str(row["applied_by"]),
    )


def _error_code(error: BaseException) -> str:
    sqlstate = getattr(error, "sqlstate", None)
    if isinstance(sqlstate, str) and sqlstate:
        return sqlstate
    return type(error).__name__


async def _attempt_rollback(connection: Any) -> bool:
    try:
        await connection.rollback()
    except BaseException:
        return False
    return True
