"""Domain model for reviewed, atomic PostgreSQL migrations.

The migration aggregate is deliberately database-independent.  It captures the
exact forward and compensating statements, their conservative PostgreSQL
behaviour classification, and the hashes a reviewer approves before any
connection is acquired.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any
from typing import Mapping

PLAN_VERSION = 1
MAX_MIGRATION_STEPS = 100
MAX_MIGRATION_SQL_CHARACTERS = 100_000
MAX_MIGRATION_TOTAL_CHARACTERS = 1_000_000
MAX_MIGRATION_NAME_CHARACTERS = 255

_MIGRATION_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,254}$")
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


class MigrationBehavior(str, Enum):
    """Operational behaviour assigned to one PostgreSQL statement."""

    TRANSACTIONAL = "transactional"
    NON_TRANSACTIONAL = "non_transactional"
    EXTERNAL_SIDE_EFFECT = "external_side_effect"
    FORBIDDEN = "forbidden"


class MigrationOperationStatus(str, Enum):
    """Stable outcomes returned by apply and rollback use cases."""

    APPLIED = "applied"
    ALREADY_APPLIED = "already_applied"
    ROLLED_BACK = "rolled_back"
    ALREADY_ROLLED_BACK = "already_rolled_back"


class MigrationError(RuntimeError):
    """Base class for expected migration-domain failures."""


class MigrationValidationError(MigrationError, ValueError):
    """A migration request is invalid before database work begins."""


class MigrationReviewMismatch(MigrationError):  # noqa: N818
    """A supplied review hash does not identify the reviewed aggregate."""


class MigrationNotApplyable(MigrationError):  # noqa: N818
    """A plan contains behaviour that the atomic executor cannot promise."""


class MigrationConflictError(MigrationError):
    """Persisted migration state conflicts with the requested reviewed plan."""


class MigrationOrderError(MigrationError):
    """A rollback request violates latest-first reverse order."""


class MigrationExecutionError(MigrationError):
    """Database execution failed with explicit rollback and commit certainty."""

    def __init__(
        self,
        message: str,
        *,
        phase: str,
        failed_step: int | None = None,
        rollback_confirmed: bool = False,
        commit_state: str | None = None,
    ) -> None:
        super().__init__(message)
        self.phase = phase
        self.failed_step = failed_step
        self.rolled_back = rollback_confirmed
        self.commit_state = commit_state or "not_committed"

    def to_payload(self) -> dict[str, Any]:
        """Return a response that never overstates transaction certainty."""
        return {
            "status": "failed",
            "committed": False,
            "commit_state": self.commit_state,
            "rolled_back": self.rolled_back,
            "phase": self.phase,
            "failed_step": self.failed_step,
            "error": str(self),
        }


@dataclass(frozen=True, slots=True)
class MigrationStepDraft:
    """Caller-supplied forward and compensating SQL before classification."""

    sql: str
    rollback_sql: str


@dataclass(frozen=True, slots=True)
class MigrationStep:
    """One classified step in a reviewed migration aggregate."""

    position: int
    sql: str
    rollback_sql: str
    statement_kind: str
    rollback_statement_kind: str
    behavior: MigrationBehavior
    rollback_behavior: MigrationBehavior
    warnings: tuple[str, ...] = ()
    rollback_warnings: tuple[str, ...] = ()

    def canonical_payload(self) -> dict[str, Any]:
        """Return the complete stable representation bound by the review hash."""
        return {
            "position": self.position,
            "sql": self.sql,
            "rollback_sql": self.rollback_sql,
            "statement_kind": self.statement_kind,
            "rollback_statement_kind": self.rollback_statement_kind,
            "behavior": self.behavior.value,
            "rollback_behavior": self.rollback_behavior.value,
            "warnings": list(self.warnings),
            "rollback_warnings": list(self.rollback_warnings),
        }

    def checksum_payload(self) -> dict[str, Any]:
        """Return forward content used for the execution-content checksum."""
        return {
            "position": self.position,
            "sql": self.sql,
            "statement_kind": self.statement_kind,
            "behavior": self.behavior.value,
        }


@dataclass(frozen=True, slots=True)
class MigrationPlan:
    """Aggregate root whose hashes bind reviewed migration semantics."""

    name: str
    steps: tuple[MigrationStep, ...]
    checksum: str
    review_hash: str
    warnings: tuple[str, ...]
    plan_version: int = PLAN_VERSION

    @property
    def applyable(self) -> bool:
        """Whether every forward and rollback step is fully transactional."""
        return all(
            step.behavior is MigrationBehavior.TRANSACTIONAL and step.rollback_behavior is MigrationBehavior.TRANSACTIONAL for step in self.steps
        )

    @property
    def reversible(self) -> bool:
        """Whether every step has a classified compensating statement."""
        return bool(self.steps) and all(step.rollback_sql and step.rollback_behavior is not MigrationBehavior.FORBIDDEN for step in self.steps)

    def canonical_payload(self) -> dict[str, Any]:
        """Return the exact plan representation stored in the trusted ledger."""
        return {
            "plan_version": self.plan_version,
            "name": self.name,
            "steps": [step.canonical_payload() for step in self.steps],
            "warnings": list(self.warnings),
        }

    def checksum_payload(self) -> dict[str, Any]:
        """Return forward execution content under a separate hash domain."""
        return {
            "plan_version": self.plan_version,
            "name": self.name,
            "steps": [step.checksum_payload() for step in self.steps],
        }

    def to_payload(self) -> dict[str, Any]:
        """Return the public review contract, including SQL for human review."""
        payload = self.canonical_payload()
        payload.update(
            {
                "checksum": self.checksum,
                "review_hash": self.review_hash,
                "applyable": self.applyable,
                "reversible": self.reversible,
            }
        )
        return payload

    def assert_integrity(self) -> None:
        """Recompute both hash domains and reject mutated aggregate content."""
        if not _SHA256_HEX.fullmatch(self.checksum):
            raise MigrationValidationError("checksum must be a lowercase 64-character SHA-256 digest")
        if not _SHA256_HEX.fullmatch(self.review_hash):
            raise MigrationValidationError("review_hash must be a lowercase 64-character SHA-256 digest")
        expected_checksum = _digest("pgsql-mcp:migration-checksum:v1", self.checksum_payload())
        expected_review = _digest("pgsql-mcp:migration-review:v1", self.canonical_payload())
        if self.checksum != expected_checksum:
            if self.review_hash != expected_review:
                raise MigrationValidationError("canonical content does not match the stored checksum and review_hash")
            raise MigrationValidationError("checksum does not match canonical content")
        if self.review_hash != expected_review:
            raise MigrationValidationError("review_hash does not match canonical content")

    @classmethod
    def from_canonical_payload(
        cls,
        payload: Mapping[str, Any],
        *,
        checksum: str,
        review_hash: str,
    ) -> MigrationPlan:
        """Restore and verify a plan read from the migration ledger."""
        if set(payload) != {"plan_version", "name", "steps", "warnings"}:
            raise MigrationValidationError("stored migration plan has an invalid canonical shape")
        plan_version = payload["plan_version"]
        name = payload["name"]
        raw_steps = payload["steps"]
        raw_warnings = payload["warnings"]
        if plan_version != PLAN_VERSION:
            raise MigrationValidationError(f"unsupported migration plan version {plan_version!r}")
        if not isinstance(name, str):
            raise MigrationValidationError("stored migration name must be text")
        if not isinstance(raw_steps, list) or not isinstance(raw_warnings, list):
            raise MigrationValidationError("stored migration plan has invalid steps or warnings")
        steps: list[MigrationStep] = []
        expected_step_keys = {
            "position",
            "sql",
            "rollback_sql",
            "statement_kind",
            "rollback_statement_kind",
            "behavior",
            "rollback_behavior",
            "warnings",
            "rollback_warnings",
        }
        for raw in raw_steps:
            if not isinstance(raw, Mapping) or set(raw) != expected_step_keys:
                raise MigrationValidationError("stored migration step has an invalid canonical shape")
            try:
                warnings = raw["warnings"]
                rollback_warnings = raw["rollback_warnings"]
                if not isinstance(warnings, list) or not all(isinstance(item, str) for item in warnings):
                    raise TypeError
                if not isinstance(rollback_warnings, list) or not all(isinstance(item, str) for item in rollback_warnings):
                    raise TypeError
                step = MigrationStep(
                    position=int(raw["position"]),
                    sql=_require_text(raw["sql"], "sql"),
                    rollback_sql=_require_text(raw["rollback_sql"], "rollback_sql"),
                    statement_kind=_require_text(raw["statement_kind"], "statement_kind"),
                    rollback_statement_kind=_require_text(raw["rollback_statement_kind"], "rollback_statement_kind"),
                    behavior=MigrationBehavior(_require_text(raw["behavior"], "behavior")),
                    rollback_behavior=MigrationBehavior(_require_text(raw["rollback_behavior"], "rollback_behavior")),
                    warnings=tuple(warnings),
                    rollback_warnings=tuple(rollback_warnings),
                )
            except (TypeError, ValueError, KeyError) as exc:
                raise MigrationValidationError("stored migration step contains invalid values") from exc
            steps.append(step)
        if not all(isinstance(item, str) for item in raw_warnings):
            raise MigrationValidationError("stored migration warnings must be text")
        plan = cls(
            name=name,
            steps=tuple(steps),
            checksum=checksum.lower(),
            review_hash=review_hash.lower(),
            warnings=tuple(raw_warnings),
            plan_version=plan_version,
        )
        plan.assert_integrity()
        return plan


@dataclass(frozen=True, slots=True)
class AppliedMigration:
    """Redacted metadata for one trusted migration-ledger row."""

    migration_id: int
    name: str
    checksum: str
    review_hash: str
    plan_version: int
    batch: int
    step_count: int
    applied_at: Any
    applied_by: str

    def to_payload(self) -> dict[str, Any]:
        """Return ledger metadata without forward or rollback SQL."""
        applied_at = self.applied_at.isoformat() if hasattr(self.applied_at, "isoformat") else str(self.applied_at)
        return {
            "migration_id": self.migration_id,
            "name": self.name,
            "checksum": self.checksum,
            "review_hash": self.review_hash,
            "plan_version": self.plan_version,
            "batch": self.batch,
            "step_count": self.step_count,
            "applied_at": applied_at,
            "applied_by": self.applied_by,
        }


@dataclass(frozen=True, slots=True)
class MigrationStatusSnapshot:
    """Bounded migration history returned by the status use case."""

    migrations: tuple[AppliedMigration, ...]

    def to_payload(self) -> dict[str, Any]:
        return {
            "total_returned": len(self.migrations),
            "migrations": [migration.to_payload() for migration in self.migrations],
        }


@dataclass(frozen=True, slots=True)
class MigrationOperationResult:
    """Result of one successfully resolved apply or rollback request."""

    status: MigrationOperationStatus
    migration: AppliedMigration | None

    def to_payload(self) -> dict[str, Any]:
        committed = self.status in {
            MigrationOperationStatus.APPLIED,
            MigrationOperationStatus.ALREADY_APPLIED,
            MigrationOperationStatus.ROLLED_BACK,
        }
        changed = self.status in {MigrationOperationStatus.APPLIED, MigrationOperationStatus.ROLLED_BACK}
        idempotent = self.status in {
            MigrationOperationStatus.ALREADY_APPLIED,
            MigrationOperationStatus.ALREADY_ROLLED_BACK,
        }
        return {
            "status": self.status.value,
            "committed": committed,
            "database_changed": changed,
            "idempotent": idempotent,
            "migration": self.migration.to_payload() if self.migration is not None else None,
        }


def validate_migration_name(name: str) -> str:
    """Normalize and validate the stable ledger identity for a migration."""
    normalized = name.strip()
    if not normalized:
        raise MigrationValidationError("migration name must not be empty")
    if len(normalized) > MAX_MIGRATION_NAME_CHARACTERS or not _MIGRATION_NAME.fullmatch(normalized):
        raise MigrationValidationError("migration name may contain only letters, digits, dots, underscores, and hyphens")
    return normalized


def normalize_review_hash(review_hash: str) -> str:
    """Normalize and validate a caller-supplied review digest."""
    normalized = review_hash.strip().lower()
    if not _SHA256_HEX.fullmatch(normalized):
        raise MigrationReviewMismatch("review_hash must be a 64-character hexadecimal SHA-256 digest")
    return normalized


def _digest(domain: str, payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(domain.encode("ascii") + b"\0" + encoded).hexdigest()


def build_plan_hashes(*, name: str, steps: tuple[MigrationStep, ...], warnings: tuple[str, ...]) -> tuple[str, str]:
    """Build separate execution-content and full-review hashes."""
    placeholder = MigrationPlan(name=name, steps=steps, checksum="0" * 64, review_hash="0" * 64, warnings=warnings)
    checksum = _digest("pgsql-mcp:migration-checksum:v1", placeholder.checksum_payload())
    review_hash = _digest("pgsql-mcp:migration-review:v1", placeholder.canonical_payload())
    return checksum, review_hash


def _require_text(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise MigrationValidationError(f"stored migration {label} must be text")
    return value
