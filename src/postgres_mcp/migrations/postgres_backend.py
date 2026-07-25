"""PostgreSQL adapter for the reviewed migration application service."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Mapping
from typing import Any

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from ..sql import SqlDriver
from .domain import AppliedMigration
from .domain import MigrationConflictError
from .domain import MigrationExecutionError
from .domain import MigrationOperationResult
from .domain import MigrationOperationStatus
from .domain import MigrationOrderError
from .domain import MigrationPlan
from .domain import MigrationReviewMismatch
from .domain import MigrationStatusSnapshot
from .domain import MigrationValidationError

LEDGER_TABLE_NAME = "_postgres_mcp_migrations"
_LEDGER_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ADVISORY_LOCK_NAMESPACE = "pgsql-mcp:reviewed-migrations:v1"

_EXPECTED_COLUMNS: tuple[tuple[str, int, bool, str], ...] = (
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


class PostgresMigrationBackend:
    """Apply reviewed DDL and ledger state on one PostgreSQL transaction."""

    def __init__(
        self,
        sql_driver: SqlDriver,
        *,
        ledger_schema: str = "public",
        ledger_table: str = LEDGER_TABLE_NAME,
    ) -> None:
        self.sql_driver = sql_driver
        self.ledger_schema = _validated_identifier(ledger_schema, "ledger_schema")
        self.ledger_table = _validated_identifier(ledger_table, "ledger_table")
        self.qualified_ledger = f'"{self.ledger_schema}"."{self.ledger_table}"'
        self.ledger_regclass = f"{self.ledger_schema}.{self.ledger_table}"

    async def apply(
        self,
        plan: MigrationPlan,
        *,
        timeout_seconds: int,
        lock_timeout_seconds: int,
    ) -> MigrationOperationResult:
        active_step: int | None = None
        try:
            async with asyncio.timeout(timeout_seconds):
                async with self.sql_driver.connection() as connection:
                    transaction_started = False
                    try:
                        await self._begin(connection, read_only=False, timeout_seconds=timeout_seconds, lock_timeout_seconds=lock_timeout_seconds)
                        transaction_started = True
                        await self._ensure_ledger(connection)
                        await self._validate_ledger(connection)
                        existing_row = await self._get_by_name(connection, plan.name)
                        if existing_row is not None:
                            stored_plan = self._verified_plan(existing_row)
                            if stored_plan.checksum != plan.checksum or stored_plan.review_hash != plan.review_hash:
                                raise MigrationConflictError(f"migration {plan.name!r} already exists with different reviewed content")
                            await connection.commit()
                            transaction_started = False
                            return MigrationOperationResult(
                                MigrationOperationStatus.ALREADY_APPLIED,
                                _applied_from_row(existing_row),
                            )

                        batch = await self._next_batch(connection)
                        for index, step in enumerate(plan.steps):
                            active_step = index
                            async with connection.cursor() as cursor:
                                await cursor.execute(step.sql)
                        inserted = await self._insert_ledger(connection, plan, batch=batch)
                        try:
                            await connection.commit()
                        except BaseException as exc:
                            if transaction_started:
                                await _attempt_rollback(connection)
                            raise MigrationExecutionError(
                                f"migration commit outcome is unknown: {exc}",
                                phase="commit",
                                failed_step=active_step,
                                rollback_confirmed=False,
                                commit_state="unknown",
                            ) from exc
                        transaction_started = False
                        return MigrationOperationResult(
                            MigrationOperationStatus.APPLIED,
                            _applied_from_row(inserted),
                        )
                    except (MigrationConflictError, MigrationReviewMismatch, MigrationOrderError):
                        if transaction_started:
                            await _attempt_rollback(connection)
                        raise
                    except MigrationExecutionError:
                        raise
                    except BaseException as exc:
                        rolled_back = await _attempt_rollback(connection) if transaction_started else False
                        if isinstance(exc, (KeyboardInterrupt, SystemExit, asyncio.CancelledError)):
                            raise
                        raise MigrationExecutionError(
                            f"migration apply failed: {exc}",
                            phase="apply",
                            failed_step=active_step,
                            rollback_confirmed=rolled_back,
                            commit_state="not_committed",
                        ) from exc
        except TimeoutError as exc:
            raise MigrationExecutionError(
                "migration apply timed out",
                phase="apply",
                failed_step=active_step,
                rollback_confirmed=True,
            ) from exc

    async def rollback(
        self,
        *,
        name: str,
        review_hash: str,
        timeout_seconds: int,
        lock_timeout_seconds: int,
    ) -> MigrationOperationResult:
        active_step: int | None = None
        try:
            async with asyncio.timeout(timeout_seconds):
                async with self.sql_driver.connection() as connection:
                    transaction_started = False
                    try:
                        await self._begin(connection, read_only=False, timeout_seconds=timeout_seconds, lock_timeout_seconds=lock_timeout_seconds)
                        transaction_started = True
                        if not await self._ledger_exists(connection):
                            await connection.rollback()
                            transaction_started = False
                            return MigrationOperationResult(MigrationOperationStatus.ALREADY_ROLLED_BACK, None)
                        await self._validate_ledger(connection)
                        row = await self._get_for_rollback(connection, name)
                        if row is None:
                            await connection.rollback()
                            transaction_started = False
                            return MigrationOperationResult(MigrationOperationStatus.ALREADY_ROLLED_BACK, None)
                        try:
                            plan = self._verified_plan(row)
                        except MigrationConflictError:
                            raise
                        if plan.review_hash != review_hash.lower():
                            raise MigrationReviewMismatch("supplied review_hash does not match the stored reviewed migration")
                        latest_id = row.get("latest_migration_id", row.get("migration_id"))
                        if latest_id is not None and int(latest_id) != int(row["migration_id"]):
                            raise MigrationOrderError(f"migration {name!r} is not the latest applied migration")

                        for reverse_index, step in enumerate(reversed(plan.steps)):
                            active_step = len(plan.steps) - 1 - reverse_index
                            async with connection.cursor() as cursor:
                                await cursor.execute(step.rollback_sql)
                        async with connection.cursor() as cursor:
                            await cursor.execute(
                                f"DELETE FROM {self.qualified_ledger} WHERE id = %s",
                                [row["migration_id"]],
                            )
                        try:
                            await connection.commit()
                        except BaseException as exc:
                            await _attempt_rollback(connection)
                            raise MigrationExecutionError(
                                f"migration rollback commit outcome is unknown: {exc}",
                                phase="commit",
                                failed_step=active_step,
                                rollback_confirmed=False,
                                commit_state="unknown",
                            ) from exc
                        transaction_started = False
                        return MigrationOperationResult(
                            MigrationOperationStatus.ROLLED_BACK,
                            _applied_from_row(row),
                        )
                    except (MigrationConflictError, MigrationReviewMismatch, MigrationOrderError):
                        if transaction_started:
                            await _attempt_rollback(connection)
                        raise
                    except MigrationExecutionError:
                        raise
                    except BaseException as exc:
                        rolled_back = await _attempt_rollback(connection) if transaction_started else False
                        if isinstance(exc, (KeyboardInterrupt, SystemExit, asyncio.CancelledError)):
                            raise
                        raise MigrationExecutionError(
                            f"migration rollback failed: {exc}",
                            phase="rollback",
                            failed_step=active_step,
                            rollback_confirmed=rolled_back,
                            commit_state="not_committed",
                        ) from exc
        except TimeoutError as exc:
            raise MigrationExecutionError(
                "migration rollback timed out",
                phase="rollback",
                failed_step=active_step,
                rollback_confirmed=True,
            ) from exc

    async def status(self, *, limit: int) -> MigrationStatusSnapshot:
        async with self.sql_driver.connection() as connection:
            transaction_started = False
            try:
                await self._begin(connection, read_only=True, timeout_seconds=30, lock_timeout_seconds=5, acquire_lock=False)
                transaction_started = True
                if not await self._ledger_exists(connection):
                    await connection.rollback()
                    transaction_started = False
                    return MigrationStatusSnapshot(())
                await self._validate_ledger(connection)
                async with connection.cursor(row_factory=dict_row) as cursor:
                    await cursor.execute(
                        f"""
                        SELECT
                            id AS migration_id,
                            name,
                            checksum,
                            review_hash,
                            plan_version,
                            batch,
                            step_count,
                            applied_at,
                            applied_by
                        FROM {self.qualified_ledger}
                        ORDER BY id ASC
                        LIMIT %s
                        """,
                        [limit],
                    )
                    rows = await cursor.fetchall()
                await connection.rollback()
                transaction_started = False
                return MigrationStatusSnapshot(tuple(_applied_from_row(_mapping(row)) for row in rows))
            except BaseException:
                if transaction_started:
                    await _attempt_rollback(connection)
                raise

    async def _begin(
        self,
        connection: Any,
        *,
        read_only: bool,
        timeout_seconds: int,
        lock_timeout_seconds: int,
        acquire_lock: bool = True,
    ) -> None:
        async with connection.cursor() as cursor:
            mode = "READ ONLY" if read_only else "READ WRITE"
            await cursor.execute(f"BEGIN ISOLATION LEVEL SERIALIZABLE {mode}")
            await cursor.execute(
                "SELECT set_config('statement_timeout', %s, true)",
                [f"{max(1, int(timeout_seconds * 1000))}ms"],
            )
            await cursor.execute(
                "SELECT set_config('lock_timeout', %s, true)",
                [f"{max(1, int(lock_timeout_seconds * 1000))}ms"],
            )
            await cursor.execute(
                "SELECT set_config('idle_in_transaction_session_timeout', %s, true)",
                [f"{max(1, int(timeout_seconds * 1000))}ms"],
            )
            await cursor.execute("SELECT set_config('row_security', 'on', true)")
            await cursor.execute("SELECT set_config('search_path', 'pg_catalog', true)")
            if acquire_lock:
                await cursor.execute(
                    "SELECT pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(%s, 0))",
                    [f"{_ADVISORY_LOCK_NAMESPACE}:{self.ledger_regclass}"],
                )

    async def _ensure_ledger(self, connection: Any) -> None:
        async with connection.cursor() as cursor:
            await cursor.execute(f'CREATE SCHEMA IF NOT EXISTS "{self.ledger_schema}"')
            await cursor.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self.qualified_ledger} (
                    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                    name text NOT NULL UNIQUE,
                    checksum text NOT NULL,
                    review_hash text NOT NULL,
                    plan_version integer NOT NULL,
                    batch integer NOT NULL,
                    step_count integer NOT NULL,
                    plan jsonb NOT NULL,
                    applied_at timestamp with time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    applied_by name NOT NULL DEFAULT CURRENT_USER
                )
                """
            )
            await cursor.execute(f'CREATE INDEX IF NOT EXISTS "{self.ledger_table}_batch_idx" ON {self.qualified_ledger} (batch, id)')

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
        trusted_relation = bool(relation) and all(
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
        if not trusted_relation:
            raise MigrationConflictError("migration ledger does not satisfy the trusted ledger contract")

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
            raise MigrationConflictError("migration ledger does not satisfy the trusted ledger contract")

    async def _get_by_name(self, connection: Any, name: str) -> dict[str, Any] | None:
        async with connection.cursor(row_factory=dict_row) as cursor:
            await cursor.execute(
                f"""
                SELECT
                    id AS migration_id,
                    name,
                    checksum,
                    review_hash,
                    plan_version,
                    batch,
                    step_count,
                    plan,
                    applied_at,
                    applied_by
                FROM {self.qualified_ledger}
                WHERE name = %s
                """,
                [name],
            )
            row = await cursor.fetchone()
        return _mapping(row) if row is not None else None

    async def _get_for_rollback(self, connection: Any, name: str) -> dict[str, Any] | None:
        async with connection.cursor(row_factory=dict_row) as cursor:
            await cursor.execute(
                f"""
                SELECT
                    id AS migration_id,
                    name,
                    checksum,
                    review_hash,
                    plan_version,
                    batch,
                    step_count,
                    plan,
                    applied_at,
                    applied_by,
                    (SELECT max(id) FROM {self.qualified_ledger}) AS latest_migration_id
                FROM {self.qualified_ledger}
                WHERE name = %s
                """,
                [name],
            )
            row = await cursor.fetchone()
        return _mapping(row) if row is not None else None

    async def _next_batch(self, connection: Any) -> int:
        async with connection.cursor(row_factory=dict_row) as cursor:
            await cursor.execute(f"SELECT COALESCE(max(batch), 0) + 1 AS batch FROM {self.qualified_ledger}")
            row = await cursor.fetchone()
        if isinstance(row, Mapping):
            return int(row["batch"])
        if row:
            return int(row[0])
        return 1

    async def _insert_ledger(self, connection: Any, plan: MigrationPlan, *, batch: int) -> dict[str, Any]:
        async with connection.cursor(row_factory=dict_row) as cursor:
            await cursor.execute(
                f"""
                INSERT INTO {self.qualified_ledger}
                    (name, checksum, review_hash, plan_version, batch, step_count, plan)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING
                    id AS migration_id,
                    name,
                    checksum,
                    review_hash,
                    plan_version,
                    batch,
                    step_count,
                    plan,
                    applied_at,
                    applied_by
                """,
                [
                    plan.name,
                    plan.checksum,
                    plan.review_hash,
                    plan.plan_version,
                    batch,
                    len(plan.steps),
                    Jsonb(plan.canonical_payload()),
                ],
            )
            row = await cursor.fetchone()
        if row is None:
            raise RuntimeError("migration ledger insert did not return a row")
        return _mapping(row)

    @staticmethod
    def _verified_plan(row: Mapping[str, Any]) -> MigrationPlan:
        try:
            return MigrationPlan.from_canonical_payload(
                row["plan"],
                checksum=str(row["checksum"]),
                review_hash=str(row["review_hash"]),
            )
        except (MigrationValidationError, KeyError, TypeError, ValueError) as exc:
            raise MigrationConflictError("migration ledger contains a corrupted reviewed plan") from exc


def _validated_identifier(value: str, label: str) -> str:
    normalized = value.strip()
    if not _LEDGER_IDENTIFIER.fullmatch(normalized):
        raise ValueError(f"{label} must be one unquoted PostgreSQL identifier")
    return normalized


def _mapping(row: Any) -> dict[str, Any]:
    if isinstance(row, Mapping):
        return dict(row)
    return {}


def _applied_from_row(row: Mapping[str, Any]) -> AppliedMigration:
    return AppliedMigration(
        migration_id=int(row["migration_id"]),
        name=str(row["name"]),
        checksum=str(row["checksum"]),
        review_hash=str(row["review_hash"]),
        plan_version=int(row["plan_version"]),
        batch=int(row["batch"]),
        step_count=int(row["step_count"]),
        applied_at=row["applied_at"],
        applied_by=str(row["applied_by"]),
    )


async def _attempt_rollback(connection: Any) -> bool:
    try:
        await connection.rollback()
    except BaseException:
        return False
    return True
