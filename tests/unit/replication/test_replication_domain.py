"""Domain contracts for replication and high-availability diagnostics."""

from __future__ import annotations

from dataclasses import replace

import pytest

from postgres_mcp.replication import ArchiveStatus
from postgres_mcp.replication import HealthSeverity
from postgres_mcp.replication import ReplicationRole
from postgres_mcp.replication import ReplicationSender
from postgres_mcp.replication import ReplicationSettings
from postgres_mcp.replication import ReplicationSlot
from postgres_mcp.replication import ReplicationSnapshot
from postgres_mcp.replication import ReplicationThresholds
from postgres_mcp.replication import ReplicationValidationError
from postgres_mcp.replication import Subscription
from postgres_mcp.replication import WalReceiver
from postgres_mcp.replication import evaluate_replication_health


def settings(
    *,
    role: ReplicationRole = ReplicationRole.PRIMARY,
    synchronous: str = "",
    replay_paused: bool = False,
) -> ReplicationSettings:
    return ReplicationSettings(
        server_version_num=180000,
        database="app",
        current_user="monitor",
        role=role,
        wal_level="replica",
        max_wal_senders=10,
        max_replication_slots=10,
        hot_standby=True,
        archive_mode="off",
        synchronous_standby_names=synchronous,
        current_wal_lsn="0/2000000",
        replay_paused=replay_paused,
        captured_at="2026-07-26T00:00:00Z",
    )


def sender(**overrides) -> ReplicationSender:
    values = {
        "pid": 42,
        "user_name": "replicator",
        "application_name": "standby-a",
        "client_address": "10.0.0.2",
        "state": "streaming",
        "sync_state": "async",
        "sent_lsn": "0/2000000",
        "write_lsn": "0/2000000",
        "flush_lsn": "0/2000000",
        "replay_lsn": "0/2000000",
        "write_pending_bytes": 0,
        "flush_pending_bytes": 0,
        "replay_pending_bytes": 0,
        "write_lag_seconds": 0.0,
        "flush_lag_seconds": 0.0,
        "replay_lag_seconds": 0.0,
        "reply_time": None,
    }
    values.update(overrides)
    return ReplicationSender(**values)


def receiver(*, lag: float | None = 0.0) -> WalReceiver:
    return WalReceiver(
        pid=99,
        status="streaming",
        sender_host="primary",
        sender_port=5432,
        slot_name="standby_slot",
        receive_start_lsn="0/1000000",
        written_lsn="0/2000000",
        flushed_lsn="0/2000000",
        latest_end_lsn="0/2000000",
        last_message_receipt_time=None,
        latest_end_time=None,
        replay_lsn="0/2000000",
        replay_timestamp=None,
        replay_lag_seconds=lag,
    )


def slot(**overrides) -> ReplicationSlot:
    values = {
        "slot_name": "consumer",
        "slot_type": "logical",
        "plugin": "pgoutput",
        "database": "app",
        "temporary": False,
        "active": True,
        "active_pid": 43,
        "restart_lsn": "0/1000000",
        "confirmed_flush_lsn": "0/2000000",
        "retained_bytes": 0,
        "wal_status": "reserved",
        "safe_wal_size": None,
        "two_phase": False,
    }
    values.update(overrides)
    return ReplicationSlot(**values)


def subscription(**overrides) -> Subscription:
    values = {
        "oid": 7,
        "name": "analytics",
        "owner": "app",
        "enabled": True,
        "slot_name": "analytics",
        "synchronous_commit": "off",
        "publications": ("events",),
        "worker_count": 1,
        "latest_end_lsn": "0/2000000",
        "last_message_receipt_time": None,
        "latest_end_time": None,
        "tables_not_ready": 0,
    }
    values.update(overrides)
    return Subscription(**values)


def test_thresholds_are_ordered_positive_and_type_safe() -> None:
    ReplicationThresholds()
    with pytest.raises(ReplicationValidationError, match="greater than zero"):
        ReplicationThresholds(warning_lag_seconds=0)
    with pytest.raises(ReplicationValidationError, match="positive integer"):
        ReplicationThresholds(warning_lag_bytes=True)
    with pytest.raises(ReplicationValidationError, match="lower"):
        ReplicationThresholds(warning_lag_seconds=300, critical_lag_seconds=30)
    with pytest.raises(ReplicationValidationError, match="lower"):
        ReplicationThresholds(warning_lag_bytes=100, critical_lag_bytes=100)
    with pytest.raises(ReplicationValidationError, match="lower"):
        ReplicationThresholds(warning_slot_retained_bytes=100, critical_slot_retained_bytes=100)


def test_primary_snapshot_reports_sender_slot_subscription_and_archive_risks() -> None:
    thresholds = ReplicationThresholds(
        warning_lag_seconds=10,
        critical_lag_seconds=100,
        warning_lag_bytes=100,
        critical_lag_bytes=1000,
        warning_slot_retained_bytes=100,
        critical_slot_retained_bytes=1000,
    )
    snapshot = ReplicationSnapshot(
        settings=settings(synchronous="FIRST 1 (standby-a)"),
        senders=(sender(state="catchup", replay_pending_bytes=1500, replay_lag_seconds=50),),
        slots=(
            slot(active=False, retained_bytes=500, wal_status="unreserved"),
            slot(slot_name="lost", wal_status="lost"),
        ),
        subscriptions=(
            subscription(enabled=False),
            subscription(name="stalled", worker_count=0, tables_not_ready=2),
        ),
        archive=ArchiveStatus(
            archived_count=10,
            failed_count=2,
            last_archived_wal=None,
            last_archived_time=None,
            last_failed_wal="0001",
            last_failed_time=None,
            stats_reset=None,
        ),
    )

    warnings = evaluate_replication_health(snapshot, thresholds)
    codes = {warning.code for warning in warnings}

    assert {
        "sender_not_streaming",
        "sender_wal_lag",
        "sender_replay_lag",
        "synchronous_standby_missing",
        "inactive_slot_retained_wal",
        "replication_slot_unreserved",
        "replication_slot_lost",
        "subscription_disabled",
        "subscription_worker_missing",
        "subscription_tables_not_ready",
        "wal_archive_failures",
    } <= codes
    resolved = snapshot.with_warnings(warnings)
    assert resolved.health is HealthSeverity.CRITICAL
    assert resolved.to_payload()["redactions"] == ["subscription_connection_info"]
    assert "conninfo" not in str(resolved.to_payload()).lower()


def test_standby_findings_cover_missing_receiver_pause_and_lag() -> None:
    missing = ReplicationSnapshot(settings=settings(role=ReplicationRole.STANDBY, replay_paused=True))
    missing_codes = {warning.code for warning in evaluate_replication_health(missing, ReplicationThresholds())}
    assert missing_codes == {"standby_receiver_missing", "standby_replay_paused"}

    lagged = replace(missing, receiver=receiver(lag=350), settings=settings(role=ReplicationRole.STANDBY))
    warnings = evaluate_replication_health(lagged, ReplicationThresholds())
    assert [(warning.code, warning.severity) for warning in warnings] == [
        ("standby_replay_lag", HealthSeverity.CRITICAL)
    ]


def test_healthy_snapshot_is_informational_and_has_stable_payload() -> None:
    snapshot = ReplicationSnapshot(
        settings=settings(),
        senders=(sender(),),
        slots=(slot(),),
        subscriptions=(subscription(),),
    )
    warnings = evaluate_replication_health(snapshot, ReplicationThresholds())
    healthy = snapshot.with_warnings(warnings)

    assert warnings == ()
    assert healthy.health is HealthSeverity.INFO
    assert healthy.to_payload()["settings"]["role"] == "primary"
