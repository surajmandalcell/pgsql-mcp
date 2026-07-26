"""Domain contracts for PostgreSQL replication and failover readiness."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from datetime import timezone

import pytest

from postgres_mcp.replication import FailoverReadinessAssessment
from postgres_mcp.replication import FindingSeverity
from postgres_mcp.replication import LogicalSubscription
from postgres_mcp.replication import NodeRole
from postgres_mcp.replication import Publication
from postgres_mcp.replication import ReplicationFinding
from postgres_mcp.replication import ReplicationSlot
from postgres_mcp.replication import ReplicationStandby
from postgres_mcp.replication import ReplicationThresholds
from postgres_mcp.replication import ReplicationTopology
from postgres_mcp.replication import ReplicationValidationError
from postgres_mcp.replication import WalReceiver
from postgres_mcp.replication import assess_failover_readiness

NOW = datetime(2026, 7, 26, tzinfo=timezone.utc)


def standby(**overrides):
    values = {
        "application_name": "standby-a",
        "client_address": "10.0.0.2",
        "state": "streaming",
        "sync_state": "async",
        "backend_start": NOW,
        "sent_lsn": "0/100",
        "write_lsn": "0/100",
        "flush_lsn": "0/100",
        "replay_lsn": "0/100",
        "write_lag_seconds": 0.1,
        "flush_lag_seconds": 0.2,
        "replay_lag_seconds": 0.3,
        "replay_lag_bytes": 1024,
    }
    values.update(overrides)
    return ReplicationStandby(**values)


def slot(**overrides):
    values = {
        "slot_name": "slot_a",
        "slot_type": "physical",
        "database": None,
        "plugin": None,
        "active": True,
        "active_pid": 123,
        "temporary": False,
        "restart_lsn": "0/100",
        "confirmed_flush_lsn": None,
        "retained_wal_bytes": 0,
        "wal_status": "reserved",
        "safe_wal_size_bytes": 1024,
        "conflicting": False,
    }
    values.update(overrides)
    return ReplicationSlot(**values)


def receiver(status: str = "streaming") -> WalReceiver:
    return WalReceiver(status, "slot_a", "primary.internal", 5432, "0/100", "0/100", NOW, NOW, NOW)


def subscription(*, active: bool = True) -> LogicalSubscription:
    return LogicalSubscription("analytics_sub", 321 if active else None, None, "0/100", "0/100", NOW, NOW, NOW)


def publication() -> Publication:
    return Publication("events_pub", False, True, True, True, True, False)


def topology(*, role: NodeRole = NodeRole.PRIMARY, **overrides) -> ReplicationTopology:
    values = {
        "captured_at": NOW,
        "server_version_num": 180001,
        "database": "app",
        "current_user": "monitor",
        "role": role,
        "transaction_read_only": role is NodeRole.STANDBY,
        "wal_level": "replica",
        "max_wal_senders": 10,
        "max_replication_slots": 10,
        "hot_standby": True,
        "synchronous_standby_names_configured": False,
        "replay_paused": False,
        "current_wal_lsn": None if role is NodeRole.STANDBY else "0/200",
        "received_wal_lsn": "0/200" if role is NodeRole.STANDBY else None,
        "replayed_wal_lsn": "0/200" if role is NodeRole.STANDBY else None,
        "replay_lag_bytes": 0 if role is NodeRole.STANDBY else None,
        "standbys": (standby(),) if role is NodeRole.PRIMARY else (),
        "slots": (slot(),),
        "wal_receiver": receiver() if role is NodeRole.STANDBY else None,
        "subscriptions": (subscription(),),
        "publications": (publication(),),
        "unavailable": (),
    }
    values.update(overrides)
    return ReplicationTopology(**values)


def test_topology_payload_is_bounded_secret_free_and_json_ready() -> None:
    payload = topology().to_payload()
    assert payload["captured_at"] == NOW.isoformat()
    assert payload["role"] == "primary"
    assert payload["standbys"][0]["backend_start"] == NOW.isoformat()
    assert payload["slots"][0]["slot_name"] == "slot_a"
    assert payload["subscriptions"][0]["active"] is True
    assert payload["publications"][0]["name"] == "events_pub"
    assert payload["wal_receiver"] is None
    assert "conninfo" not in str(payload).lower()
    assert "password" not in str(payload).lower()


def test_value_payloads_preserve_operational_fields() -> None:
    finding = ReplicationFinding("slot_warning", FindingSeverity.WARNING, "Slot retains WAL.", {"retained_wal_bytes": 42}, "Inspect.")
    assert standby().to_payload()["replay_lag_seconds"] == 0.3
    assert receiver().to_payload()["latest_end_time"] == NOW.isoformat()
    assert slot().to_payload()["safe_wal_size_bytes"] == 1024
    assert subscription(active=False).to_payload()["active"] is False
    assert publication().to_payload()["publish_truncate"] is True
    assert finding.to_payload()["severity"] == "warning"
    with pytest.raises(TypeError):
        finding.evidence["mutate"] = True  # type: ignore[index]


@pytest.mark.parametrize(
    "factory,match",
    [
        (lambda: standby(application_name=" "), "application_name"),
        (lambda: standby(state=""), "state"),
        (lambda: standby(sync_state="\x00"), "NUL"),
        (lambda: standby(replay_lag_bytes=-1), "negative"),
        (lambda: standby(replay_lag_bytes="bad"), "numeric"),
        (lambda: slot(slot_name=""), "slot name"),
        (lambda: slot(slot_type=""), "slot type"),
        (lambda: slot(retained_wal_bytes=-1), "negative"),
        (lambda: receiver(""), "receiver status"),
        (lambda: LogicalSubscription("", None, None, None, None, None, None, None), "subscription name"),
        (lambda: replace(publication(), name="\x00"), "NUL"),
        (lambda: replace(topology(), captured_at=datetime(2026, 7, 26)), "timezone-aware"),
        (lambda: replace(topology(), server_version_num=1), "server_version_num"),
        (lambda: replace(topology(), max_wal_senders=-1), "negative"),
    ],
)
def test_domain_rejects_invalid_values(factory, match: str) -> None:
    with pytest.raises(ReplicationValidationError, match=match):
        factory()


def test_thresholds_require_ordered_non_negative_values() -> None:
    ReplicationThresholds(warning_lag_bytes=0, critical_lag_bytes=0)
    with pytest.raises(ReplicationValidationError, match="critical lag bytes"):
        ReplicationThresholds(warning_lag_bytes=2, critical_lag_bytes=1)
    with pytest.raises(ReplicationValidationError, match="warning lag seconds"):
        ReplicationThresholds(warning_lag_seconds=-1)
    with pytest.raises(ReplicationValidationError, match="inactive slot bytes"):
        ReplicationThresholds(warning_inactive_slot_bytes=5, critical_inactive_slot_bytes=4)


def test_healthy_primary_is_ready() -> None:
    result = assess_failover_readiness(topology())
    assert result.status == "ready"
    assert result.ready is True
    assert result.findings == ()
    assert result.to_payload()["topology_summary"]["standby_count"] == 1


def test_primary_finds_sync_loss_state_lag_and_time_lag() -> None:
    thresholds = ReplicationThresholds(warning_lag_bytes=100, critical_lag_bytes=1_000, warning_lag_seconds=10, critical_lag_seconds=30)
    result = assess_failover_readiness(topology(standbys=(standby(state="catchup", replay_lag_bytes=2_000, replay_lag_seconds=20),)), thresholds)
    assert {item.code: item.severity for item in result.findings} == {
        "standby_not_streaming": FindingSeverity.WARNING,
        "standby_wal_lag": FindingSeverity.CRITICAL,
        "standby_time_lag": FindingSeverity.WARNING,
    }
    assert result.status == "critical"


def test_primary_with_required_sync_standby_missing_is_critical() -> None:
    result = assess_failover_readiness(topology(synchronous_standby_names_configured=True, standbys=()))
    assert [item.code for item in result.findings] == ["synchronous_standby_missing"]


def test_standby_receiver_replay_and_lag_failures_are_critical() -> None:
    result = assess_failover_readiness(topology(role=NodeRole.STANDBY, wal_receiver=receiver("stopped"), replay_paused=True, replay_lag_bytes=100), ReplicationThresholds(warning_lag_bytes=10, critical_lag_bytes=100))
    assert {item.code for item in result.findings} == {"standby_receiver_inactive", "standby_replay_paused", "standby_replay_lag"}
    assert all(item.severity is FindingSeverity.CRITICAL for item in result.findings)


def test_standby_without_receiver_is_critical() -> None:
    result = assess_failover_readiness(topology(role=NodeRole.STANDBY, wal_receiver=None))
    assert result.findings[0].to_payload()["evidence"] == {"receiver_status": None}


def test_slots_subscriptions_and_visibility_produce_bounded_findings() -> None:
    result = assess_failover_readiness(
        topology(
            slots=(slot(slot_name="inactive", active=False, retained_wal_bytes=100), slot(slot_name="lost", wal_status="lost"), slot(slot_name="conflict", conflicting=True)),
            subscriptions=(subscription(active=False),),
            unavailable=("replication_slots", "logical_subscriptions"),
        ),
        ReplicationThresholds(warning_inactive_slot_bytes=100, critical_inactive_slot_bytes=1_000),
    )
    assert {item.code for item in result.findings} == {
        "inactive_slot_wal_retention", "replication_slot_lost", "replication_slot_conflicting", "logical_subscription_inactive", "replication_visibility_incomplete"
    }
    assert result.status == "critical"


def test_warning_only_assessment_is_not_ready() -> None:
    result = assess_failover_readiness(topology(subscriptions=(subscription(active=False),)))
    assert result.status == "warning"
    assert result.ready is False


def test_assessment_rejects_invalid_status_and_freezes_summary() -> None:
    with pytest.raises(ReplicationValidationError, match="status"):
        FailoverReadinessAssessment("unknown", False, (), {})
    assessment = FailoverReadinessAssessment("ready", True, (), {"role": "primary"})
    with pytest.raises(TypeError):
        assessment.topology_summary["role"] = "standby"  # type: ignore[index]
