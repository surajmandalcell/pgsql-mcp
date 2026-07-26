"""Immutable domain model for PostgreSQL replication and failover readiness."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from enum import Enum
from types import MappingProxyType
from typing import Any

MAX_REPLICATION_ROWS = 100
_MIB = 1024 * 1024
_GIB = 1024 * _MIB


class ReplicationError(Exception):
    """Base error for the replication bounded context."""


class ReplicationValidationError(ReplicationError, ValueError):
    """Raised when a replication request or snapshot violates an invariant."""


class ReplicationExecutionError(ReplicationError):
    """Raised when a consistent read-only replication snapshot cannot be captured."""


class NodeRole(str, Enum):
    """PostgreSQL node role derived from recovery state."""

    PRIMARY = "primary"
    STANDBY = "standby"


class FindingSeverity(str, Enum):
    """Operational severity for failover-readiness findings."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


def _non_negative(value: int | float | None, *, label: str) -> int | float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ReplicationValidationError(f"{label} must be numeric")
    if value < 0:
        raise ReplicationValidationError(f"{label} cannot be negative")
    return value


def _text(value: str, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReplicationValidationError(f"{label} must not be empty")
    if "\x00" in value:
        raise ReplicationValidationError(f"{label} must not contain NUL")
    return value


def _iso(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


@dataclass(frozen=True, slots=True)
class ReplicationStandby:
    """One sender process visible in ``pg_stat_replication``."""

    application_name: str
    client_address: str | None
    state: str
    sync_state: str
    backend_start: Any | None = None
    sent_lsn: str | None = None
    write_lsn: str | None = None
    flush_lsn: str | None = None
    replay_lsn: str | None = None
    write_lag_seconds: float | None = None
    flush_lag_seconds: float | None = None
    replay_lag_seconds: float | None = None
    replay_lag_bytes: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "application_name", _text(self.application_name, label="standby application_name"))
        object.__setattr__(self, "state", _text(self.state, label="standby state"))
        object.__setattr__(self, "sync_state", _text(self.sync_state, label="standby sync_state"))
        for name in ("write_lag_seconds", "flush_lag_seconds", "replay_lag_seconds", "replay_lag_bytes"):
            _non_negative(getattr(self, name), label=name)

    def to_payload(self) -> dict[str, Any]:
        return {
            "application_name": self.application_name,
            "client_address": self.client_address,
            "state": self.state,
            "sync_state": self.sync_state,
            "backend_start": _iso(self.backend_start),
            "sent_lsn": self.sent_lsn,
            "write_lsn": self.write_lsn,
            "flush_lsn": self.flush_lsn,
            "replay_lsn": self.replay_lsn,
            "write_lag_seconds": self.write_lag_seconds,
            "flush_lag_seconds": self.flush_lag_seconds,
            "replay_lag_seconds": self.replay_lag_seconds,
            "replay_lag_bytes": self.replay_lag_bytes,
        }


@dataclass(frozen=True, slots=True)
class ReplicationSlot:
    """Physical or logical slot with bounded retained-WAL metadata."""

    slot_name: str
    slot_type: str
    database: str | None
    plugin: str | None
    active: bool
    active_pid: int | None
    temporary: bool
    restart_lsn: str | None
    confirmed_flush_lsn: str | None
    retained_wal_bytes: int | None
    wal_status: str | None
    safe_wal_size_bytes: int | None
    conflicting: bool | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "slot_name", _text(self.slot_name, label="replication slot name"))
        object.__setattr__(self, "slot_type", _text(self.slot_type, label="replication slot type"))
        _non_negative(self.retained_wal_bytes, label="retained_wal_bytes")
        _non_negative(self.safe_wal_size_bytes, label="safe_wal_size_bytes")

    def to_payload(self) -> dict[str, Any]:
        return {
            "slot_name": self.slot_name,
            "slot_type": self.slot_type,
            "database": self.database,
            "plugin": self.plugin,
            "active": self.active,
            "active_pid": self.active_pid,
            "temporary": self.temporary,
            "restart_lsn": self.restart_lsn,
            "confirmed_flush_lsn": self.confirmed_flush_lsn,
            "retained_wal_bytes": self.retained_wal_bytes,
            "wal_status": self.wal_status,
            "safe_wal_size_bytes": self.safe_wal_size_bytes,
            "conflicting": self.conflicting,
        }


@dataclass(frozen=True, slots=True)
class WalReceiver:
    """Secret-free status from ``pg_stat_wal_receiver``."""

    status: str
    slot_name: str | None
    sender_host: str | None
    sender_port: int | None
    received_lsn: str | None
    latest_end_lsn: str | None
    last_msg_send_time: Any | None
    last_msg_receipt_time: Any | None
    latest_end_time: Any | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", _text(self.status, label="WAL receiver status"))

    def to_payload(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "slot_name": self.slot_name,
            "sender_host": self.sender_host,
            "sender_port": self.sender_port,
            "received_lsn": self.received_lsn,
            "latest_end_lsn": self.latest_end_lsn,
            "last_msg_send_time": _iso(self.last_msg_send_time),
            "last_msg_receipt_time": _iso(self.last_msg_receipt_time),
            "latest_end_time": _iso(self.latest_end_time),
        }


@dataclass(frozen=True, slots=True)
class LogicalSubscription:
    """One logical-subscription worker without connection secrets."""

    subscription_name: str
    worker_pid: int | None
    relation_oid: int | None
    received_lsn: str | None
    latest_end_lsn: str | None
    last_msg_send_time: Any | None
    last_msg_receipt_time: Any | None
    latest_end_time: Any | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "subscription_name", _text(self.subscription_name, label="subscription name"))

    @property
    def active(self) -> bool:
        return self.worker_pid is not None

    def to_payload(self) -> dict[str, Any]:
        return {
            "subscription_name": self.subscription_name,
            "active": self.active,
            "worker_pid": self.worker_pid,
            "relation_oid": self.relation_oid,
            "received_lsn": self.received_lsn,
            "latest_end_lsn": self.latest_end_lsn,
            "last_msg_send_time": _iso(self.last_msg_send_time),
            "last_msg_receipt_time": _iso(self.last_msg_receipt_time),
            "latest_end_time": _iso(self.latest_end_time),
        }


@dataclass(frozen=True, slots=True)
class Publication:
    """Logical-publication behavior relevant to failover planning."""

    name: str
    all_tables: bool
    publish_insert: bool
    publish_update: bool
    publish_delete: bool
    publish_truncate: bool
    publish_via_partition_root: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _text(self.name, label="publication name"))

    def to_payload(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "all_tables": self.all_tables,
            "publish_insert": self.publish_insert,
            "publish_update": self.publish_update,
            "publish_delete": self.publish_delete,
            "publish_truncate": self.publish_truncate,
            "publish_via_partition_root": self.publish_via_partition_root,
        }


@dataclass(frozen=True, slots=True)
class ReplicationTopology:
    """Bounded, secret-free replication snapshot from one PostgreSQL node."""

    captured_at: datetime
    server_version_num: int
    database: str
    current_user: str
    role: NodeRole
    transaction_read_only: bool
    wal_level: str
    max_wal_senders: int
    max_replication_slots: int
    hot_standby: bool
    synchronous_standby_names_configured: bool
    replay_paused: bool
    current_wal_lsn: str | None
    received_wal_lsn: str | None
    replayed_wal_lsn: str | None
    replay_lag_bytes: int | None
    standbys: tuple[ReplicationStandby, ...] = ()
    slots: tuple[ReplicationSlot, ...] = ()
    wal_receiver: WalReceiver | None = None
    subscriptions: tuple[LogicalSubscription, ...] = ()
    publications: tuple[Publication, ...] = ()
    unavailable: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.captured_at.tzinfo is None:
            raise ReplicationValidationError("captured_at must be timezone-aware")
        if self.server_version_num < 10000:
            raise ReplicationValidationError("server_version_num is invalid")
        object.__setattr__(self, "database", _text(self.database, label="database"))
        object.__setattr__(self, "current_user", _text(self.current_user, label="current_user"))
        object.__setattr__(self, "role", NodeRole(self.role))
        object.__setattr__(self, "wal_level", _text(self.wal_level, label="wal_level"))
        for name in ("max_wal_senders", "max_replication_slots", "replay_lag_bytes"):
            _non_negative(getattr(self, name), label=name)
        object.__setattr__(self, "standbys", tuple(self.standbys))
        object.__setattr__(self, "slots", tuple(self.slots))
        object.__setattr__(self, "subscriptions", tuple(self.subscriptions))
        object.__setattr__(self, "publications", tuple(self.publications))
        object.__setattr__(self, "unavailable", tuple(sorted(set(self.unavailable))))

    def to_payload(self) -> dict[str, Any]:
        return {
            "captured_at": self.captured_at.isoformat(),
            "server_version_num": self.server_version_num,
            "database": self.database,
            "current_user": self.current_user,
            "role": self.role.value,
            "transaction_read_only": self.transaction_read_only,
            "wal_level": self.wal_level,
            "max_wal_senders": self.max_wal_senders,
            "max_replication_slots": self.max_replication_slots,
            "hot_standby": self.hot_standby,
            "synchronous_standby_names_configured": self.synchronous_standby_names_configured,
            "replay_paused": self.replay_paused,
            "current_wal_lsn": self.current_wal_lsn,
            "received_wal_lsn": self.received_wal_lsn,
            "replayed_wal_lsn": self.replayed_wal_lsn,
            "replay_lag_bytes": self.replay_lag_bytes,
            "standbys": [item.to_payload() for item in self.standbys],
            "slots": [item.to_payload() for item in self.slots],
            "wal_receiver": self.wal_receiver.to_payload() if self.wal_receiver else None,
            "subscriptions": [item.to_payload() for item in self.subscriptions],
            "publications": [item.to_payload() for item in self.publications],
            "unavailable": list(self.unavailable),
        }


@dataclass(frozen=True, slots=True)
class ReplicationThresholds:
    """Operator-controlled thresholds for deterministic readiness findings."""

    warning_lag_bytes: int = 64 * _MIB
    critical_lag_bytes: int = _GIB
    warning_lag_seconds: float = 30.0
    critical_lag_seconds: float = 300.0
    warning_inactive_slot_bytes: int = _GIB
    critical_inactive_slot_bytes: int = 10 * _GIB

    def __post_init__(self) -> None:
        pairs = (
            ("lag bytes", self.warning_lag_bytes, self.critical_lag_bytes),
            ("lag seconds", self.warning_lag_seconds, self.critical_lag_seconds),
            ("inactive slot bytes", self.warning_inactive_slot_bytes, self.critical_inactive_slot_bytes),
        )
        for label, warning, critical in pairs:
            _non_negative(warning, label=f"warning {label}")
            _non_negative(critical, label=f"critical {label}")
            if critical < warning:
                raise ReplicationValidationError(f"critical {label} must be greater than or equal to warning {label}")


@dataclass(frozen=True, slots=True)
class ReplicationFinding:
    """One deterministic readiness finding with structured evidence."""

    code: str
    severity: FindingSeverity
    summary: str
    evidence: Mapping[str, Any] = field(default_factory=dict)
    recommended_action: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", _text(self.code, label="finding code"))
        object.__setattr__(self, "severity", FindingSeverity(self.severity))
        object.__setattr__(self, "summary", _text(self.summary, label="finding summary"))
        object.__setattr__(self, "evidence", MappingProxyType(dict(self.evidence)))

    def to_payload(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity.value,
            "summary": self.summary,
            "evidence": dict(self.evidence),
            "recommended_action": self.recommended_action,
        }


@dataclass(frozen=True, slots=True)
class FailoverReadinessAssessment:
    """Answer-first assessment derived only from a captured topology."""

    status: str
    ready: bool
    findings: tuple[ReplicationFinding, ...]
    topology_summary: Mapping[str, Any]

    def __post_init__(self) -> None:
        if self.status not in {"ready", "warning", "critical"}:
            raise ReplicationValidationError("assessment status is invalid")
        object.__setattr__(self, "findings", tuple(self.findings))
        object.__setattr__(self, "topology_summary", MappingProxyType(dict(self.topology_summary)))

    def to_payload(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "ready": self.ready,
            "topology_summary": dict(self.topology_summary),
            "findings": [finding.to_payload() for finding in self.findings],
        }


def _severity_for(value: int | float, *, warning: int | float, critical: int | float) -> FindingSeverity | None:
    if value >= critical:
        return FindingSeverity.CRITICAL
    if value >= warning:
        return FindingSeverity.WARNING
    return None


def assess_failover_readiness(
    topology: ReplicationTopology,
    thresholds: ReplicationThresholds | None = None,
) -> FailoverReadinessAssessment:
    """Derive deterministic failover findings without additional database access."""
    thresholds = thresholds or ReplicationThresholds()
    findings: list[ReplicationFinding] = []

    def add(code: str, severity: FindingSeverity, summary: str, evidence: Mapping[str, Any], action: str) -> None:
        findings.append(ReplicationFinding(code, severity, summary, evidence, action))

    if topology.role is NodeRole.STANDBY:
        if topology.wal_receiver is None or topology.wal_receiver.status.lower() != "streaming":
            add(
                "standby_receiver_inactive",
                FindingSeverity.CRITICAL,
                "The standby does not have an active streaming WAL receiver.",
                {"receiver_status": topology.wal_receiver.status if topology.wal_receiver else None},
                "Restore streaming replication and verify the upstream connection before promotion planning.",
            )
        if topology.replay_paused:
            add(
                "standby_replay_paused",
                FindingSeverity.CRITICAL,
                "WAL replay is paused on the standby.",
                {},
                "Resume WAL replay and verify replay progress before considering promotion.",
            )
        if topology.replay_lag_bytes is not None:
            severity = _severity_for(
                topology.replay_lag_bytes,
                warning=thresholds.warning_lag_bytes,
                critical=thresholds.critical_lag_bytes,
            )
            if severity:
                add(
                    "standby_replay_lag",
                    severity,
                    "The standby has material receive-to-replay lag.",
                    {"replay_lag_bytes": topology.replay_lag_bytes},
                    "Investigate replay blockers and storage or CPU pressure before failover.",
                )
    elif topology.synchronous_standby_names_configured and not topology.standbys:
        add(
            "synchronous_standby_missing",
            FindingSeverity.CRITICAL,
            "Synchronous replication is configured but no standby sender is visible.",
            {},
            "Restore the required synchronous standby or intentionally revise the synchronous policy.",
        )

    for standby in topology.standbys:
        if standby.state.lower() != "streaming":
            add(
                "standby_not_streaming",
                FindingSeverity.WARNING,
                f"Standby {standby.application_name} is not streaming.",
                {"application_name": standby.application_name, "state": standby.state},
                "Inspect the sender and standby logs and restore streaming state.",
            )
        if standby.replay_lag_bytes is not None:
            severity = _severity_for(
                standby.replay_lag_bytes,
                warning=thresholds.warning_lag_bytes,
                critical=thresholds.critical_lag_bytes,
            )
            if severity:
                add(
                    "standby_wal_lag",
                    severity,
                    f"Standby {standby.application_name} exceeds the WAL lag threshold.",
                    {"application_name": standby.application_name, "replay_lag_bytes": standby.replay_lag_bytes},
                    "Resolve network, receiver, replay, or storage pressure before relying on this standby.",
                )
        if standby.replay_lag_seconds is not None:
            severity = _severity_for(
                standby.replay_lag_seconds,
                warning=thresholds.warning_lag_seconds,
                critical=thresholds.critical_lag_seconds,
            )
            if severity:
                add(
                    "standby_time_lag",
                    severity,
                    f"Standby {standby.application_name} exceeds the replay-time threshold.",
                    {"application_name": standby.application_name, "replay_lag_seconds": standby.replay_lag_seconds},
                    "Confirm the standby can catch up within the recovery objective.",
                )

    for slot in topology.slots:
        if slot.conflicting:
            add(
                "replication_slot_conflicting",
                FindingSeverity.CRITICAL,
                f"Replication slot {slot.slot_name} is conflicting on this standby.",
                {"slot_name": slot.slot_name},
                "Recreate or repair the slot only after validating downstream replication state.",
            )
        if slot.wal_status == "lost":
            add(
                "replication_slot_lost",
                FindingSeverity.CRITICAL,
                f"Replication slot {slot.slot_name} has lost required WAL.",
                {"slot_name": slot.slot_name},
                "Reinitialize the affected replica or subscriber from a valid base state.",
            )
        if not slot.active and slot.retained_wal_bytes is not None:
            severity = _severity_for(
                slot.retained_wal_bytes,
                warning=thresholds.warning_inactive_slot_bytes,
                critical=thresholds.critical_inactive_slot_bytes,
            )
            if severity:
                add(
                    "inactive_slot_wal_retention",
                    severity,
                    f"Inactive slot {slot.slot_name} is retaining WAL.",
                    {"slot_name": slot.slot_name, "retained_wal_bytes": slot.retained_wal_bytes},
                    "Confirm the consumer is expected to return; otherwise remove the slot through an administrator workflow.",
                )

    for subscription in topology.subscriptions:
        if not subscription.active:
            add(
                "logical_subscription_inactive",
                FindingSeverity.WARNING,
                f"Logical subscription {subscription.subscription_name} has no active worker.",
                {"subscription_name": subscription.subscription_name},
                "Inspect subscription state and worker logs before relying on logical replication continuity.",
            )

    if topology.unavailable:
        add(
            "replication_visibility_incomplete",
            FindingSeverity.WARNING,
            "Some replication catalogs were unavailable to the current role.",
            {"unavailable": list(topology.unavailable)},
            "Grant only the minimum monitoring privileges needed for complete diagnostics.",
        )

    severities = {finding.severity for finding in findings}
    status = "critical" if FindingSeverity.CRITICAL in severities else "warning" if FindingSeverity.WARNING in severities else "ready"
    return FailoverReadinessAssessment(
        status=status,
        ready=status == "ready",
        findings=tuple(findings),
        topology_summary={
            "role": topology.role.value,
            "standby_count": len(topology.standbys),
            "slot_count": len(topology.slots),
            "active_subscription_count": sum(item.active for item in topology.subscriptions),
            "publication_count": len(topology.publications),
            "visibility_complete": not topology.unavailable,
        },
    )
