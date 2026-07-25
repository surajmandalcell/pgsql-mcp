"""Domain model for reviewed PostgreSQL maintenance operations."""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any
from typing import Mapping

PLAN_VERSION = 1
MAX_IDENTIFIER_BYTES = 63
MAX_MAINTENANCE_NAME_CHARACTERS = 200
_REVIEW_DOMAIN = b"pgsql-mcp:reviewed-maintenance:v1\0"
_ALLOWED_INDEX_CLEANUP = frozenset({"auto", "on", "off"})
_ALLOWED_PERSISTENCE = frozenset({"p", "u", "t"})


class MaintenanceError(Exception):
    """Base error for the reviewed-maintenance bounded context."""


class MaintenanceValidationError(MaintenanceError, ValueError):
    """Raised when a structured maintenance request violates an invariant."""


class MaintenanceReviewMismatch(MaintenanceError):
    """Raised when the supplied review hash does not identify the plan."""


class MaintenanceConflictError(MaintenanceError):
    """Raised when durable maintenance state conflicts with a request."""


class MaintenanceBusyError(MaintenanceError):
    """Raised when another maintenance session owns the target lock."""


class MaintenanceExecutionError(MaintenanceError):
    """Raised when a nontransactional maintenance operation cannot finish safely."""

    def __init__(
        self,
        message: str,
        *,
        phase: str,
        outcome: str,
        error_code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.phase = phase
        self.outcome = outcome
        self.error_code = error_code

    def to_payload(self) -> dict[str, Any]:
        return {
            "error": str(self),
            "phase": self.phase,
            "outcome": self.outcome,
            "error_code": self.error_code,
            "rollback_available": False,
        }


class MaintenanceOperation(str, Enum):
    """Structured public operations with known PostgreSQL semantics."""

    VACUUM_ANALYZE = "vacuum_analyze"
    ANALYZE = "analyze"
    REINDEX_INDEX_CONCURRENTLY = "reindex_index_concurrently"
    REFRESH_MATERIALIZED_VIEW_CONCURRENTLY = "refresh_materialized_view_concurrently"


class MaintenanceOperationStatus(str, Enum):
    """Durable execution states for nontransactional work."""

    RUNNING = "running"
    SUCCEEDED = "succeeded"
    ALREADY_SUCCEEDED = "already_succeeded"
    FAILED = "failed"
    UNKNOWN = "unknown"
    RECONCILED_SUCCEEDED = "reconciled_succeeded"
    RECONCILED_FAILED = "reconciled_failed"


class ReconciliationResolution(str, Enum):
    """Explicit operator resolution for an unknown maintenance outcome."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"


def _checked_identifier(value: str, *, label: str) -> str:
    if not isinstance(value, str):
        raise MaintenanceValidationError(f"{label} must be a string")
    if not value or not value.strip():
        raise MaintenanceValidationError(f"{label} must not be empty")
    if "\x00" in value:
        raise MaintenanceValidationError(f"{label} must not contain NUL")
    if len(value.encode("utf-8")) > MAX_IDENTIFIER_BYTES:
        raise MaintenanceValidationError(f"{label} cannot exceed {MAX_IDENTIFIER_BYTES} UTF-8 bytes")
    return value


def _checked_name(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MaintenanceValidationError("maintenance name must not be empty")
    if "\x00" in value:
        raise MaintenanceValidationError("maintenance name must not contain NUL")
    if len(value) > MAX_MAINTENANCE_NAME_CHARACTERS:
        raise MaintenanceValidationError(
            f"maintenance name cannot exceed {MAX_MAINTENANCE_NAME_CHARACTERS} characters"
        )
    return value


def _checked_review_hash(value: str) -> str:
    normalized = value.lower()
    if len(normalized) != 64 or any(character not in "0123456789abcdef" for character in normalized):
        raise ValueError("review_hash must be a 64-character lowercase hexadecimal digest")
    return normalized


@dataclass(frozen=True, slots=True)
class MaintenanceTarget:
    """Schema-qualified PostgreSQL relation identity."""

    schema: str
    name: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "schema", _checked_identifier(self.schema, label="schema"))
        object.__setattr__(self, "name", _checked_identifier(self.name, label="target name"))


@dataclass(frozen=True, slots=True)
class MaintenanceOptions:
    """Conservative options shared by supported maintenance commands."""

    skip_locked: bool = False
    index_cleanup: str = "auto"
    parallel: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.skip_locked, bool):
            raise MaintenanceValidationError("skip_locked must be a boolean")
        normalized_cleanup = str(self.index_cleanup).lower()
        if normalized_cleanup not in _ALLOWED_INDEX_CLEANUP:
            raise MaintenanceValidationError("index_cleanup must be auto, on, or off")
        object.__setattr__(self, "index_cleanup", normalized_cleanup)
        if not isinstance(self.parallel, int) or isinstance(self.parallel, bool):
            raise MaintenanceValidationError("parallel must be an integer")
        if self.parallel < 0 or self.parallel > 1024:
            raise MaintenanceValidationError("parallel must be between 0 and 1024")

    @property
    def is_default(self) -> bool:
        return not self.skip_locked and self.index_cleanup == "auto" and self.parallel == 0

    def to_payload(self) -> dict[str, Any]:
        return {
            "skip_locked": self.skip_locked,
            "index_cleanup": self.index_cleanup,
            "parallel": self.parallel,
        }


@dataclass(frozen=True, slots=True)
class MaintenanceRequest:
    """Caller intent before live PostgreSQL target inspection."""

    name: str
    operation: MaintenanceOperation
    target: MaintenanceTarget
    options: MaintenanceOptions = MaintenanceOptions()

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _checked_name(self.name))
        try:
            object.__setattr__(self, "operation", MaintenanceOperation(self.operation))
        except ValueError as exc:
            raise MaintenanceValidationError(f"unsupported maintenance operation: {self.operation!r}") from exc
        if not isinstance(self.target, MaintenanceTarget):
            raise MaintenanceValidationError("target must be a MaintenanceTarget")
        if not isinstance(self.options, MaintenanceOptions):
            raise MaintenanceValidationError("options must be MaintenanceOptions")


@dataclass(frozen=True, slots=True)
class TargetSnapshot:
    """Live target identity and preconditions resolved from PostgreSQL catalogs."""

    oid: int
    relation_kind: str
    persistence: str
    is_partition: bool
    is_populated: bool
    has_usable_unique_index: bool
    is_exclusion_index: bool

    def __post_init__(self) -> None:
        if not isinstance(self.oid, int) or isinstance(self.oid, bool) or self.oid <= 0:
            raise MaintenanceValidationError("target OID must be a positive integer")
        if not isinstance(self.relation_kind, str) or len(self.relation_kind) != 1:
            raise MaintenanceValidationError("target relation kind must be one PostgreSQL relkind")
        if self.persistence not in _ALLOWED_PERSISTENCE:
            raise MaintenanceValidationError("target persistence must be p, u, or t")
        flags = (
            self.is_partition,
            self.is_populated,
            self.has_usable_unique_index,
            self.is_exclusion_index,
        )
        if any(not isinstance(flag, bool) for flag in flags):
            raise MaintenanceValidationError("target snapshot flags must be boolean")


@dataclass(frozen=True, slots=True)
class MaintenancePlan:
    """Immutable reviewed maintenance aggregate."""

    name: str
    operation: MaintenanceOperation
    target: MaintenanceTarget
    options: MaintenanceOptions
    target_oid: int
    target_kind: str
    target_persistence: str
    is_partition: bool
    preconditions: Mapping[str, Any]
    warnings: tuple[str, ...]
    transaction_behavior: str
    review_hash: str
    plan_version: int = PLAN_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _checked_name(self.name))
        object.__setattr__(self, "operation", MaintenanceOperation(self.operation))
        if not isinstance(self.target, MaintenanceTarget):
            raise MaintenanceValidationError("target must be a MaintenanceTarget")
        if not isinstance(self.options, MaintenanceOptions):
            raise MaintenanceValidationError("options must be MaintenanceOptions")
        if not isinstance(self.target_oid, int) or isinstance(self.target_oid, bool) or self.target_oid <= 0:
            raise MaintenanceValidationError("target OID must be a positive integer")
        if not isinstance(self.target_kind, str) or len(self.target_kind) != 1:
            raise MaintenanceValidationError("target kind must be one PostgreSQL relkind")
        if self.target_persistence not in _ALLOWED_PERSISTENCE:
            raise MaintenanceValidationError("target persistence must be p, u, or t")
        if self.transaction_behavior != "non_transactional":
            raise MaintenanceValidationError("reviewed maintenance must be classified as non_transactional")
        object.__setattr__(self, "preconditions", MappingProxyType(dict(self.preconditions)))
        object.__setattr__(self, "warnings", tuple(str(item) for item in self.warnings))
        object.__setattr__(self, "review_hash", _checked_review_hash(self.review_hash))
        if self.plan_version != PLAN_VERSION:
            raise MaintenanceValidationError(f"unsupported maintenance plan version: {self.plan_version}")

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "plan_version": self.plan_version,
            "name": self.name,
            "operation": self.operation.value,
            "target": {
                "schema": self.target.schema,
                "name": self.target.name,
                "oid": self.target_oid,
                "kind": self.target_kind,
                "persistence": self.target_persistence,
                "is_partition": self.is_partition,
            },
            "options": self.options.to_payload(),
            "preconditions": dict(self.preconditions),
            "warnings": list(self.warnings),
            "transaction_behavior": self.transaction_behavior,
        }

    def expected_review_hash(self) -> str:
        canonical = json.dumps(
            self.canonical_payload(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        return hashlib.sha256(_REVIEW_DOMAIN + canonical).hexdigest()

    def assert_integrity(self) -> None:
        if not hmac.compare_digest(self.review_hash, self.expected_review_hash()):
            raise MaintenanceValidationError("maintenance plan integrity check failed")

    def to_payload(self) -> dict[str, Any]:
        payload = self.canonical_payload()
        payload["review_hash"] = self.review_hash
        payload["rollback_available"] = False
        return payload

    @classmethod
    def from_canonical_payload(
        cls,
        payload: Mapping[str, Any],
        *,
        review_hash: str,
    ) -> MaintenancePlan:
        try:
            target_payload = payload["target"]
            options_payload = payload["options"]
            if not isinstance(target_payload, Mapping) or not isinstance(options_payload, Mapping):
                raise TypeError("target or options")
            plan = cls(
                plan_version=int(payload["plan_version"]),
                name=str(payload["name"]),
                operation=MaintenanceOperation(str(payload["operation"])),
                target=MaintenanceTarget(
                    schema=str(target_payload["schema"]),
                    name=str(target_payload["name"]),
                ),
                options=MaintenanceOptions(
                    skip_locked=options_payload["skip_locked"],
                    index_cleanup=str(options_payload["index_cleanup"]),
                    parallel=options_payload["parallel"],
                ),
                target_oid=int(target_payload["oid"]),
                target_kind=str(target_payload["kind"]),
                target_persistence=str(target_payload["persistence"]),
                is_partition=target_payload["is_partition"],
                preconditions=payload["preconditions"],
                warnings=tuple(payload["warnings"]),
                transaction_behavior=str(payload["transaction_behavior"]),
                review_hash=review_hash,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise MaintenanceValidationError("invalid canonical maintenance plan") from exc
        plan.assert_integrity()
        return plan


@dataclass(frozen=True, slots=True)
class MaintenanceRecord:
    """Redacted durable status for one reviewed operation."""

    operation_id: int
    name: str
    review_hash: str
    plan_version: int
    operation: MaintenanceOperation
    target: MaintenanceTarget
    target_oid: int
    status: MaintenanceOperationStatus
    started_at: Any
    finished_at: Any | None
    error_code: str | None
    applied_by: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "name": self.name,
            "review_hash": self.review_hash,
            "plan_version": self.plan_version,
            "operation": self.operation.value,
            "target": {
                "schema": self.target.schema,
                "name": self.target.name,
                "oid": self.target_oid,
            },
            "status": self.status.value,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error_code": self.error_code,
        }


@dataclass(frozen=True, slots=True)
class MaintenanceOperationResult:
    """Result returned only after durable status handling completes."""

    status: MaintenanceOperationStatus
    record: MaintenanceRecord | None

    def to_payload(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "record": self.record.to_payload() if self.record is not None else None,
            "rollback_available": False,
        }


@dataclass(frozen=True, slots=True)
class MaintenanceStatusSnapshot:
    """Bounded redacted maintenance history."""

    operations: tuple[MaintenanceRecord, ...]

    def to_payload(self) -> dict[str, Any]:
        return {"operations": [item.to_payload() for item in self.operations]}


class MaintenancePlanner:
    """Pure planner that combines caller intent with a live target snapshot."""

    def create_plan(
        self,
        request: MaintenanceRequest,
        snapshot: TargetSnapshot,
    ) -> MaintenancePlan:
        if not isinstance(request, MaintenanceRequest):
            raise MaintenanceValidationError("request must be MaintenanceRequest")
        if not isinstance(snapshot, TargetSnapshot):
            raise MaintenanceValidationError("snapshot must be TargetSnapshot")
        if snapshot.persistence == "t":
            raise MaintenanceValidationError("temporary maintenance targets are not supported")

        operation = request.operation
        kind = snapshot.relation_kind
        if operation is MaintenanceOperation.VACUUM_ANALYZE:
            if kind not in {"r", "p", "m"}:
                raise MaintenanceValidationError("target relation kind is not supported for VACUUM ANALYZE")
        elif operation is MaintenanceOperation.ANALYZE:
            if kind not in {"r", "p", "m", "f"}:
                raise MaintenanceValidationError("target relation kind is not supported for ANALYZE")
            if request.options.index_cleanup != "auto" or request.options.parallel != 0:
                raise MaintenanceValidationError("index_cleanup and parallel are only supported for VACUUM ANALYZE")
        elif operation is MaintenanceOperation.REINDEX_INDEX_CONCURRENTLY:
            if kind not in {"i", "I"}:
                raise MaintenanceValidationError("concurrent reindex requires an index target")
            if snapshot.is_exclusion_index:
                raise MaintenanceValidationError("exclusion indexes cannot be reindexed concurrently")
            if not request.options.is_default:
                raise MaintenanceValidationError("maintenance options are only supported for VACUUM ANALYZE or ANALYZE")
        elif operation is MaintenanceOperation.REFRESH_MATERIALIZED_VIEW_CONCURRENTLY:
            if kind != "m":
                raise MaintenanceValidationError("concurrent refresh requires a materialized view target")
            if not snapshot.is_populated:
                raise MaintenanceValidationError("concurrent refresh requires a populated materialized view")
            if not snapshot.has_usable_unique_index:
                raise MaintenanceValidationError("concurrent refresh requires a usable all-row unique index")
            if not request.options.is_default:
                raise MaintenanceValidationError("maintenance options are only supported for VACUUM ANALYZE or ANALYZE")

        preconditions = {
            "is_populated": snapshot.is_populated,
            "has_usable_unique_index": snapshot.has_usable_unique_index,
            "is_exclusion_index": snapshot.is_exclusion_index,
        }
        warnings = (
            "cannot_roll_back",
            "reconciliation_required_after_unknown_outcome",
            "may_generate_substantial_io",
        )
        provisional = MaintenancePlan(
            name=request.name,
            operation=operation,
            target=request.target,
            options=request.options,
            target_oid=snapshot.oid,
            target_kind=kind,
            target_persistence=snapshot.persistence,
            is_partition=snapshot.is_partition,
            preconditions=preconditions,
            warnings=warnings,
            transaction_behavior="non_transactional",
            review_hash="0" * 64,
        )
        return MaintenancePlan(
            name=provisional.name,
            operation=provisional.operation,
            target=provisional.target,
            options=provisional.options,
            target_oid=provisional.target_oid,
            target_kind=provisional.target_kind,
            target_persistence=provisional.target_persistence,
            is_partition=provisional.is_partition,
            preconditions=provisional.preconditions,
            warnings=provisional.warnings,
            transaction_behavior=provisional.transaction_behavior,
            review_hash=provisional.expected_review_hash(),
        )
