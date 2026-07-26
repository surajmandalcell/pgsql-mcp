"""Domain model for read-only PostgreSQL replication and HA diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import replace
from enum import Enum
from types import MappingProxyType
from typing import Any
from typing import Mapping

MAX_REPLICATION_ROWS = 500


class ReplicationValidationError(ValueError):
    """Raised when a replication diagnostic request violates a domain invariant."""


class ReplicationRole(str, Enum):
    """Server role derived from ``pg_is_in_recovery``."""

    PRIMARY = "primary"
    STANDBY = "standby"


class HealthSeverity(str, Enum):
    """Ordered diagnostic severity."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        return {
            HealthSeverity.INFO: 0,
            HealthSeverity.WARNING: 1,
            HealthSeverity.CRITICAL: 2,
        }[self]


@dataclass(frozen=True, slots=True)
class ReplicationThresholds:
    """Operator-selected warning and critical thresholds."""

    warning_lag_seconds: float = 30.0
    critical_lag_seconds: float = 300.0
    warning_lag_bytes: int = 64 * 1024 * 1024
    critical_lag_bytes: int = 1024 * 1024 * 1024
    warning_slot_retained_bytes: int = 1024 * 1024 * 1024
    critical_slot_retained_bytes: int = 10 * 1024 * 1024 * 1024

    def __post_init__(self) -> None:
        for label, value in (
            ("warning_lag_seconds", self.warning_lag_seconds),
            ("critical_lag_seconds", self.critical_lag_seconds),
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
                raise ReplicationValidationError(f"{label} must be greater than zero")
        for label, value in (
            ("warning_lag_bytes", self.warning_lag_bytes),
            ("critical_lag_bytes", self.critical_lag_bytes),
            ("warning_slot_retained_bytes", self.warning_slot_retained_bytes),
            ("critical_slot_retained_bytes", self.critical_slot_retained_bytes),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ReplicationValidationError(f"{label} must be a positive integer")
        if self.warning_lag_seconds >= self.critical_lag_seconds:
            raise ReplicationValidationError("warning_lag_seconds must be lower than critical_lag_seconds")
        if self.warning_lag_bytes >= self.critical_lag_bytes:
            raise ReplicationValidationError("warning_lag_bytes must be lower than critical_lag_bytes")
        if self.warning_slot_retained_bytes >= self.critical_slot_retained_bytes:
            raise ReplicationValidationError(
                "warning_slot_retained_bytes must be lower than critical_slot_retained_bytes"
            )


@dataclass(frozen=True, slots=True)
class ReplicationWarning:
    """One deterministic finding with machine-readable evidence."""

    code: str
    severity: HealthSeverity
    message: str
    object_kind: str | None = None
    object_name: str | None = None
    evidence: Mapping[str, Any] = MappingProxyType({})

    def __post_init__(self) -> None:
        if not self.code or not self.code.strip():
            raise ReplicationValidationError("warning code must not be empty")
        if not self.message or not self.message.strip():
            raise ReplicationValidationError("warning message must not be empty")
        object.__setattr__(self, "severity", HealthSeverity(self.severity))
        object.__setattr__(self, "evidence", MappingProxyType(dict(self.evidence)))

    def to_payload(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity.value,
            "message": self.message,
            "object_kind": self.object_kind,
            "object_name": self.object_name,
            "evidence": dict(self.evidence),
        }


@dataclass(frozen=True, slots=True)
class ReplicationSettings:
    """Non-secret server settings that define replication capability."""

    server_version_num: int
    database: str
    current_user: str
    role: ReplicationRole
    wal_level: str
    max_wal_senders: int
    max_replication_slots: int
    hot_standby: bool
    archive_mode: str
    synchronous_standby_names: str
    current_wal_lsn: str | None
    replay_paused: bool
    captured_at: Any

    def to_payload(self) -> dict[str, Any]:
        return {
            "server_version_num": self.server_version_num,
            "database": self.database,
            "current_user": self.current_user,
            "role": self.role.value,
            "wal_level": self.wal_level,
            "max_wal_senders": self.max_wal_senders,
            "max_replication_slots": self.max_replication_slots,
            "hot_standby": self.hot_standby,
            "archive_mode": self.archive_mode,
            "synchronous_standby_names": self.synchronous_standby_names,
            "current_wal_lsn": self.current_wal_lsn,
            "replay_paused": self.replay_paused,
            "captured_at": self.captured_at,
        }


@dataclass(frozen=True, slots=True)
class ReplicationSender:
    """One physical or logical WAL sender visible on a primary."""

    pid: int
    user_name: str
    application_name: str
    client_address: str | None
    state: str
    sync_state: str
    sent_lsn: str | None
    write_lsn: str | None
    flush_lsn: str | None
    replay_lsn: str | None
    write_pending_bytes: int | None
    flush_pending_bytes: int | None
    replay_pending_bytes: int | None
    write_lag_seconds: float | None
    flush_lag_seconds: float | None
    replay_lag_seconds: float | None
    reply_time: Any | None

    def to_payload(self) -> dict[str, Any]:
        return {
            "pid": self.pid,
            "user_name": self.user_name,
            "application_name": self.application_name,
            "client_address": self.client_address,
            "state": self.state,
            "sync_state": self.sync_state,
            "sent_lsn": self.sent_lsn,
            "write_lsn": self.write_lsn,
            "flush_lsn": self.flush_lsn,
            "replay_lsn": self.replay_lsn,
            "write_pending_bytes": self.write_pending_bytes,
            "flush_pending_bytes": self.flush_pending_bytes,
            "replay_pending_bytes": self.replay_pending_bytes,
            "write_lag_seconds": self.write_lag_seconds,
            "flush_lag_seconds": self.flush_lag_seconds,
            "replay_lag_seconds": self.replay_lag_seconds,
            "reply_time": self.reply_time,
        }


@dataclass(frozen=True, slots=True)
class WalReceiver:
    """The current standby WAL receiver without connection credentials."""

    pid: int
    status: str
    sender_host: str | None
    sender_port: int | None
    slot_name: str | None
    receive_start_lsn: str | None
    written_lsn: str | None
    flushed_lsn: str | None
    latest_end_lsn: str | None
    last_message_receipt_time: Any | None
    latest_end_time: Any | None
    replay_lsn: str | None
    replay_timestamp: Any | None
    replay_lag_seconds: float | None

    def to_payload(self) -> dict[str, Any]:
        return {
            "pid": self.pid,
            "status": self.status,
            "sender_host": self.sender_host,
            "sender_port": self.sender_port,
            "slot_name": self.slot_name,
            "receive_start_lsn": self.receive_start_lsn,
            "written_lsn": self.written_lsn,
            "flushed_lsn": self.flushed_lsn,
            "latest_end_lsn": self.latest_end_lsn,
            "last_message_receipt_time": self.last_message_receipt_time,
            "latest_end_time": self.latest_end_time,
            "replay_lsn": self.replay_lsn,
            "replay_timestamp": self.replay_timestamp,
            "replay_lag_seconds": self.replay_lag_seconds,
        }


@dataclass(frozen=True, slots=True)
class ReplicationSlot:
    """One replication slot and its retained-WAL exposure."""

    slot_name: str
    slot_type: str
    plugin: str | None
    database: str | None
    temporary: bool
    active: bool
    active_pid: int | None
    restart_lsn: str | None
    confirmed_flush_lsn: str | None
    retained_bytes: int | None
    wal_status: str | None
    safe_wal_size: int | None
    two_phase: bool

    def to_payload(self) -> dict[str, Any]:
        return {
            "slot_name": self.slot_name,
            "slot_type": self.slot_type,
            "plugin": self.plugin,
            "database": self.database,
            "temporary": self.temporary,
            "active": self.active,
            "active_pid": self.active_pid,
            "restart_lsn": self.restart_lsn,
            "confirmed_flush_lsn": self.confirmed_flush_lsn,
            "retained_bytes": self.retained_bytes,
            "wal_status": self.wal_status,
            "safe_wal_size": self.safe_wal_size,
            "two_phase": self.two_phase,
        }


@dataclass(frozen=True, slots=True)
class Publication:
    """One logical-replication publication in the current database."""

    name: str
    owner: str
    all_tables: bool
    publish_insert: bool
    publish_update: bool
    publish_delete: bool
    publish_truncate: bool
    publish_via_partition_root: bool

    def to_payload(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "owner": self.owner,
            "all_tables": self.all_tables,
            "publish_insert": self.publish_insert,
            "publish_update": self.publish_update,
            "publish_delete": self.publish_delete,
            "publish_truncate": self.publish_truncate,
            "publish_via_partition_root": self.publish_via_partition_root,
        }


@dataclass(frozen=True, slots=True)
class Subscription:
    """One subscription with redacted connection details and worker health."""

    oid: int
    name: str
    owner: str
    enabled: bool
    slot_name: str | None
    synchronous_commit: str
    publications: tuple[str, ...]
    worker_count: int
    latest_end_lsn: str | None
    last_message_receipt_time: Any | None
    latest_end_time: Any | None
    tables_not_ready: int

    def to_payload(self) -> dict[str, Any]:
        return {
            "oid": self.oid,
            "name": self.name,
            "owner": self.owner,
            "enabled": self.enabled,
            "slot_name": self.slot_name,
            "synchronous_commit": self.synchronous_commit,
            "publications": list(self.publications),
            "worker_count": self.worker_count,
            "latest_end_lsn": self.latest_end_lsn,
            "last_message_receipt_time": self.last_message_receipt_time,
            "latest_end_time": self.latest_end_time,
            "tables_not_ready": self.tables_not_ready,
        }


@dataclass(frozen=True, slots=True)
class ArchiveStatus:
    """WAL archiver counters and latest outcomes."""

    archived_count: int
    failed_count: int
    last_archived_wal: str | None
    last_archived_time: Any | None
    last_failed_wal: str | None
    last_failed_time: Any | None
    stats_reset: Any | None

    def to_payload(self) -> dict[str, Any]:
        return {
            "archived_count": self.archived_count,
            "failed_count": self.failed_count,
            "last_archived_wal": self.last_archived_wal,
            "last_archived_time": self.last_archived_time,
            "last_failed_wal": self.last_failed_wal,
            "last_failed_time": self.last_failed_time,
            "stats_reset": self.stats_reset,
        }


@dataclass(frozen=True, slots=True)
class ReplicationSnapshot:
    """Bounded redacted snapshot of replication and HA state."""

    settings: ReplicationSettings
    senders: tuple[ReplicationSender, ...] = ()
    receiver: WalReceiver | None = None
    slots: tuple[ReplicationSlot, ...] = ()
    publications: tuple[Publication, ...] = ()
    subscriptions: tuple[Subscription, ...] = ()
    archive: ArchiveStatus | None = None
    unavailable_features: tuple[str, ...] = ()
    warnings: tuple[ReplicationWarning, ...] = ()

    @property
    def health(self) -> HealthSeverity:
        return max((warning.severity for warning in self.warnings), key=lambda item: item.rank, default=HealthSeverity.INFO)

    def with_warnings(self, warnings: tuple[ReplicationWarning, ...]) -> ReplicationSnapshot:
        return replace(self, warnings=warnings)

    def to_payload(self) -> dict[str, Any]:
        return {
            "health": self.health.value,
            "settings": self.settings.to_payload(),
            "senders": [item.to_payload() for item in self.senders],
            "receiver": self.receiver.to_payload() if self.receiver is not None else None,
            "slots": [item.to_payload() for item in self.slots],
            "publications": [item.to_payload() for item in self.publications],
            "subscriptions": [item.to_payload() for item in self.subscriptions],
            "archive": self.archive.to_payload() if self.archive is not None else None,
            "unavailable_features": list(self.unavailable_features),
            "warnings": [item.to_payload() for item in self.warnings],
            "redactions": ["subscription_connection_info"],
        }


def evaluate_replication_health(
    snapshot: ReplicationSnapshot,
    thresholds: ReplicationThresholds,
) -> tuple[ReplicationWarning, ...]:
    """Derive deterministic findings from one repository snapshot."""
    warnings: list[ReplicationWarning] = []

    if snapshot.settings.role is ReplicationRole.STANDBY:
        if snapshot.receiver is None:
            warnings.append(
                ReplicationWarning(
                    code="standby_receiver_missing",
                    severity=HealthSeverity.CRITICAL,
                    message="The server is in recovery but no WAL receiver is active.",
                )
            )
        else:
            _append_lag_warning(
                warnings,
                seconds=snapshot.receiver.replay_lag_seconds,
                warning_seconds=thresholds.warning_lag_seconds,
                critical_seconds=thresholds.critical_lag_seconds,
                code="standby_replay_lag",
                object_kind="wal_receiver",
                object_name=snapshot.receiver.sender_host,
            )
        if snapshot.settings.replay_paused:
            warnings.append(
                ReplicationWarning(
                    code="standby_replay_paused",
                    severity=HealthSeverity.WARNING,
                    message="WAL replay is paused on this standby.",
                )
            )

    for sender in snapshot.senders:
        if sender.state != "streaming":
            warnings.append(
                ReplicationWarning(
                    code="sender_not_streaming",
                    severity=HealthSeverity.WARNING,
                    message="A WAL sender is not in streaming state.",
                    object_kind="sender",
                    object_name=sender.application_name,
                    evidence={"state": sender.state, "pid": sender.pid},
                )
            )
        pending = max(
            (value for value in (sender.write_pending_bytes, sender.flush_pending_bytes, sender.replay_pending_bytes) if value is not None),
            default=None,
        )
        _append_byte_warning(
            warnings,
            value=pending,
            warning_bytes=thresholds.warning_lag_bytes,
            critical_bytes=thresholds.critical_lag_bytes,
            code="sender_wal_lag",
            object_kind="sender",
            object_name=sender.application_name,
        )
        _append_lag_warning(
            warnings,
            seconds=sender.replay_lag_seconds,
            warning_seconds=thresholds.warning_lag_seconds,
            critical_seconds=thresholds.critical_lag_seconds,
            code="sender_replay_lag",
            object_kind="sender",
            object_name=sender.application_name,
        )

    if snapshot.settings.synchronous_standby_names.strip() and not any(
        sender.state == "streaming" and sender.sync_state in {"sync", "quorum"} for sender in snapshot.senders
    ):
        warnings.append(
            ReplicationWarning(
                code="synchronous_standby_missing",
                severity=HealthSeverity.WARNING,
                message="Synchronous standby names are configured but no synchronous sender is streaming.",
                evidence={"synchronous_standby_names": snapshot.settings.synchronous_standby_names},
            )
        )

    for slot in snapshot.slots:
        if slot.wal_status == "lost":
            warnings.append(
                ReplicationWarning(
                    code="replication_slot_lost",
                    severity=HealthSeverity.CRITICAL,
                    message="A replication slot has lost required WAL and is unusable.",
                    object_kind="replication_slot",
                    object_name=slot.slot_name,
                    evidence={"wal_status": slot.wal_status},
                )
            )
        elif slot.wal_status == "unreserved":
            warnings.append(
                ReplicationWarning(
                    code="replication_slot_unreserved",
                    severity=HealthSeverity.WARNING,
                    message="A replication slot no longer has all required WAL reserved.",
                    object_kind="replication_slot",
                    object_name=slot.slot_name,
                    evidence={"wal_status": slot.wal_status},
                )
            )
        if not slot.active:
            _append_byte_warning(
                warnings,
                value=slot.retained_bytes,
                warning_bytes=thresholds.warning_slot_retained_bytes,
                critical_bytes=thresholds.critical_slot_retained_bytes,
                code="inactive_slot_retained_wal",
                object_kind="replication_slot",
                object_name=slot.slot_name,
            )

    for subscription in snapshot.subscriptions:
        if not subscription.enabled:
            warnings.append(
                ReplicationWarning(
                    code="subscription_disabled",
                    severity=HealthSeverity.WARNING,
                    message="A logical-replication subscription is disabled.",
                    object_kind="subscription",
                    object_name=subscription.name,
                )
            )
        if subscription.enabled and subscription.worker_count == 0:
            warnings.append(
                ReplicationWarning(
                    code="subscription_worker_missing",
                    severity=HealthSeverity.WARNING,
                    message="An enabled subscription has no active apply or table-sync worker.",
                    object_kind="subscription",
                    object_name=subscription.name,
                )
            )
        if subscription.tables_not_ready > 0:
            warnings.append(
                ReplicationWarning(
                    code="subscription_tables_not_ready",
                    severity=HealthSeverity.WARNING,
                    message="A subscription has tables that have not reached ready state.",
                    object_kind="subscription",
                    object_name=subscription.name,
                    evidence={"tables_not_ready": subscription.tables_not_ready},
                )
            )

    if snapshot.archive is not None and snapshot.archive.failed_count > 0:
        warnings.append(
            ReplicationWarning(
                code="wal_archive_failures",
                severity=HealthSeverity.WARNING,
                message="The WAL archiver has recorded failures since statistics were reset.",
                object_kind="archiver",
                evidence={
                    "failed_count": snapshot.archive.failed_count,
                    "last_failed_wal": snapshot.archive.last_failed_wal,
                    "last_failed_time": snapshot.archive.last_failed_time,
                },
            )
        )

    return tuple(sorted(warnings, key=lambda item: (-item.severity.rank, item.code, item.object_name or "")))


def _append_lag_warning(
    warnings: list[ReplicationWarning],
    *,
    seconds: float | None,
    warning_seconds: float,
    critical_seconds: float,
    code: str,
    object_kind: str,
    object_name: str | None,
) -> None:
    if seconds is None or seconds < warning_seconds:
        return
    severity = HealthSeverity.CRITICAL if seconds >= critical_seconds else HealthSeverity.WARNING
    warnings.append(
        ReplicationWarning(
            code=code,
            severity=severity,
            message="Replication time lag exceeds the configured threshold.",
            object_kind=object_kind,
            object_name=object_name,
            evidence={"seconds": seconds, "warning_seconds": warning_seconds, "critical_seconds": critical_seconds},
        )
    )


def _append_byte_warning(
    warnings: list[ReplicationWarning],
    *,
    value: int | None,
    warning_bytes: int,
    critical_bytes: int,
    code: str,
    object_kind: str,
    object_name: str | None,
) -> None:
    if value is None or value < warning_bytes:
        return
    severity = HealthSeverity.CRITICAL if value >= critical_bytes else HealthSeverity.WARNING
    warnings.append(
        ReplicationWarning(
            code=code,
            severity=severity,
            message="Retained or pending WAL exceeds the configured threshold.",
            object_kind=object_kind,
            object_name=object_name,
            evidence={"bytes": value, "warning_bytes": warning_bytes, "critical_bytes": critical_bytes},
        )
    )
