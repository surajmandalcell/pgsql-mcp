"""Conservative PostgreSQL DDL planning for reviewed migrations."""

from __future__ import annotations

from collections.abc import Iterable

from postgres_mcp.sql import TransactionValidationError
from postgres_mcp.sql import parse_single_statement

from .domain import MAX_MIGRATION_SQL_CHARACTERS
from .domain import MAX_MIGRATION_STEPS
from .domain import MAX_MIGRATION_TOTAL_CHARACTERS
from .domain import MigrationBehavior
from .domain import MigrationPlan
from .domain import MigrationStep
from .domain import MigrationStepDraft
from .domain import MigrationValidationError
from .domain import build_plan_hashes
from .domain import validate_migration_name


class MigrationPlanner:
    """Build immutable reviewed plans without acquiring a database connection."""

    def create_plan(self, *, name: str, steps: Iterable[MigrationStepDraft]) -> MigrationPlan:
        normalized_name = validate_migration_name(name)
        drafts = list(steps)
        if not drafts:
            raise MigrationValidationError("migration requires at least one step")
        if len(drafts) > MAX_MIGRATION_STEPS:
            raise MigrationValidationError(f"migration cannot exceed {MAX_MIGRATION_STEPS} steps")

        classified: list[MigrationStep] = []
        total_characters = 0
        for position, draft in enumerate(drafts):
            sql = _checked_sql(draft.sql, label="forward SQL")
            rollback_sql = _checked_sql(draft.rollback_sql, label="rollback SQL")
            total_characters += len(sql) + len(rollback_sql)
            if total_characters > MAX_MIGRATION_TOTAL_CHARACTERS:
                raise MigrationValidationError(f"migration SQL cannot exceed {MAX_MIGRATION_TOTAL_CHARACTERS} total characters")
            statement_kind, behavior, warnings = classify_migration_sql(sql)
            rollback_kind, rollback_behavior, rollback_warnings = classify_migration_sql(rollback_sql)
            classified.append(
                MigrationStep(
                    position=position,
                    sql=sql,
                    rollback_sql=rollback_sql,
                    statement_kind=statement_kind,
                    rollback_statement_kind=rollback_kind,
                    behavior=behavior,
                    rollback_behavior=rollback_behavior,
                    warnings=warnings,
                    rollback_warnings=rollback_warnings,
                )
            )

        step_tuple = tuple(classified)
        plan_warnings = _ordered_unique(
            warning for step in step_tuple for warning in (*step.warnings, *(f"rollback:{item}" for item in step.rollback_warnings))
        )
        checksum, review_hash = build_plan_hashes(name=normalized_name, steps=step_tuple, warnings=plan_warnings)
        plan = MigrationPlan(
            name=normalized_name,
            steps=step_tuple,
            checksum=checksum,
            review_hash=review_hash,
            warnings=plan_warnings,
        )
        plan.assert_integrity()
        return plan


def classify_migration_sql(sql: str) -> tuple[str, MigrationBehavior, tuple[str, ...]]:
    """Parse and conservatively classify one PostgreSQL migration statement."""
    checked = _checked_sql(sql, label="SQL")
    try:
        statement = parse_single_statement(checked)
    except TransactionValidationError as exc:
        message = str(exc).replace("exactly one SQL statement is required", "migration steps must contain exactly one SQL statement")
        raise MigrationValidationError(message) from exc

    kind = type(statement).__name__.removesuffix("Stmt").lower()
    normalized = " ".join(checked.upper().split())
    behavior = _behavior(kind, normalized)
    warnings = _warnings(kind, normalized, behavior)
    return kind, behavior, warnings


def _behavior(kind: str, sql: str) -> MigrationBehavior:
    forbidden_kinds = {
        "alterdatabase",
        "alterdatabaseowner",
        "alterdefaultprivileges",  # handled below as transactional when explicit
        "alterobjectdepends",
        "alteroperator",
        "altersystem",
        "call",
        "cluster",
        "copy",
        "createdatabase",
        "createpolicy",  # handled below by allowlist
        "createtablespace",
        "do",
        "dropdb",
        "droptablespace",
        "lock",
        "transaction",
        "truncate",
        "vacuum",
        "variable_set",
    }
    if kind in {"insert", "update", "delete", "merge", "select"}:
        return MigrationBehavior.FORBIDDEN
    if kind in forbidden_kinds:
        return MigrationBehavior.FORBIDDEN
    if sql.startswith("VACUUM ") or sql == "VACUUM" or sql.startswith("ALTER SYSTEM "):
        return MigrationBehavior.FORBIDDEN
    if " TEMP " in f" {sql} " or " TEMPORARY " in f" {sql} ":
        return MigrationBehavior.FORBIDDEN
    if sql.startswith("CREATE MATERIALIZED VIEW") or sql.startswith("REFRESH MATERIALIZED VIEW"):
        return MigrationBehavior.FORBIDDEN
    if sql.startswith(("CREATE DATABASE", "DROP DATABASE", "CREATE TABLESPACE", "DROP TABLESPACE", "CREATE ACCESS METHOD")):
        return MigrationBehavior.FORBIDDEN
    if sql.startswith(("CREATE INDEX CONCURRENTLY", "DROP INDEX CONCURRENTLY", "REINDEX ")) and "CONCURRENTLY" in sql:
        return MigrationBehavior.NON_TRANSACTIONAL
    if sql.startswith("ALTER TABLE") and " DETACH PARTITION " in sql and " CONCURRENTLY" in sql:
        return MigrationBehavior.NON_TRANSACTIONAL
    if sql.startswith(("CREATE EXTENSION", "ALTER EXTENSION", "DROP EXTENSION")):
        return MigrationBehavior.EXTERNAL_SIDE_EFFECT
    if sql.startswith(
        ("CREATE SERVER", "ALTER SERVER", "DROP SERVER", "CREATE FOREIGN DATA WRAPPER", "ALTER FOREIGN DATA WRAPPER", "DROP FOREIGN DATA WRAPPER")
    ):
        return MigrationBehavior.EXTERNAL_SIDE_EFFECT
    if sql.startswith(("CREATE USER MAPPING", "ALTER USER MAPPING", "DROP USER MAPPING")):
        return MigrationBehavior.EXTERNAL_SIDE_EFFECT
    if sql.startswith(("CREATE FUNCTION", "ALTER FUNCTION", "DROP FUNCTION", "CREATE PROCEDURE", "ALTER PROCEDURE", "DROP PROCEDURE")):
        return MigrationBehavior.EXTERNAL_SIDE_EFFECT
    if sql.startswith("ALTER SEQUENCE") and " RESTART" in sql:
        return MigrationBehavior.EXTERNAL_SIDE_EFFECT
    if sql.startswith(("NOTIFY ", "LISTEN ", "UNLISTEN ")):
        return MigrationBehavior.EXTERNAL_SIDE_EFFECT

    transactional_prefixes = (
        "ALTER DEFAULT PRIVILEGES",
        "ALTER DOMAIN",
        "ALTER INDEX",
        "ALTER MATERIALIZED VIEW",
        "ALTER POLICY",
        "ALTER SCHEMA",
        "ALTER SEQUENCE",
        "ALTER TABLE",
        "ALTER TYPE",
        "ALTER VIEW",
        "COMMENT ON",
        "CREATE COLLATION",
        "CREATE DOMAIN",
        "CREATE INDEX",
        "CREATE POLICY",
        "CREATE SCHEMA",
        "CREATE SEQUENCE",
        "CREATE TABLE",
        "CREATE TYPE",
        "CREATE VIEW",
        "DROP COLLATION",
        "DROP DOMAIN",
        "DROP INDEX",
        "DROP OWNED",
        "DROP POLICY",
        "DROP SCHEMA",
        "DROP SEQUENCE",
        "DROP TABLE",
        "DROP TYPE",
        "DROP VIEW",
        "GRANT ",
        "REASSIGN OWNED",
        "REVOKE ",
        "SECURITY LABEL",
    )
    if sql.startswith(transactional_prefixes):
        return MigrationBehavior.TRANSACTIONAL
    return MigrationBehavior.FORBIDDEN


def _warnings(kind: str, sql: str, behavior: MigrationBehavior) -> tuple[str, ...]:
    warnings: list[str] = []
    if sql.startswith("ALTER TABLE"):
        warnings.append("access_exclusive_lock_possible")
        if " ALTER COLUMN " in sql and " TYPE " in sql:
            warnings.append("table_rewrite_possible")
        if " SET NOT NULL" in sql or " VALIDATE CONSTRAINT" in sql:
            warnings.append("table_scan_possible")
    if sql.startswith("DROP "):
        warnings.append("destructive_drop")
    if sql.startswith(("GRANT ", "REVOKE ", "ALTER DEFAULT PRIVILEGES")):
        warnings.append("privilege_change")
    if behavior is MigrationBehavior.NON_TRANSACTIONAL:
        warnings.append("cannot_run_in_atomic_transaction")
    elif behavior is MigrationBehavior.EXTERNAL_SIDE_EFFECT:
        warnings.append("external_or_nonrollback_side_effect_possible")
    elif behavior is MigrationBehavior.FORBIDDEN:
        warnings.append("forbidden_by_migration_policy")
    if " CASCADE" in sql:
        warnings.append("cascade_dependency_changes")
    return _ordered_unique(warnings)


def _checked_sql(value: str, *, label: str) -> str:
    normalized = value.strip()
    if not normalized:
        if label == "rollback SQL":
            raise MigrationValidationError("rollback SQL must not be empty")
        raise MigrationValidationError(f"{label} must not be empty")
    if len(normalized) > MAX_MIGRATION_SQL_CHARACTERS:
        raise MigrationValidationError(f"{label} cannot exceed {MAX_MIGRATION_SQL_CHARACTERS} characters")
    return normalized


def _ordered_unique(items: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(items))
