"""Application-service contracts for replication diagnostics."""

from __future__ import annotations

from datetime import datetime
from datetime import timezone
from unittest.mock import AsyncMock

import pytest

from postgres_mcp.replication import NodeRole
from postgres_mcp.replication import ReplicationRepository
from postgres_mcp.replication import ReplicationService
from postgres_mcp.replication import ReplicationThresholds
from postgres_mcp.replication import ReplicationTopology
from postgres_mcp.replication import ReplicationValidationError


def topology() -> ReplicationTopology:
    return ReplicationTopology(
        captured_at=datetime(2026, 7, 26, tzinfo=timezone.utc),
        server_version_num=180001,
        database="app",
        current_user="monitor",
        role=NodeRole.PRIMARY,
        transaction_read_only=False,
        wal_level="replica",
        max_wal_senders=10,
        max_replication_slots=10,
        hot_standby=True,
        synchronous_standby_names_configured=False,
        replay_paused=False,
        current_wal_lsn="0/100",
        received_wal_lsn=None,
        replayed_wal_lsn=None,
        replay_lag_bytes=None,
    )


@pytest.mark.asyncio
async def test_topology_delegates_checked_limit() -> None:
    repository = AsyncMock(spec=ReplicationRepository)
    repository.capture.return_value = topology()
    service = ReplicationService(repository)

    result = await service.topology(limit=25)

    assert result.role is NodeRole.PRIMARY
    repository.capture.assert_awaited_once_with(limit=25)


@pytest.mark.asyncio
async def test_assess_captures_once_and_applies_thresholds() -> None:
    repository = AsyncMock(spec=ReplicationRepository)
    repository.capture.return_value = topology()
    service = ReplicationService(repository)
    thresholds = ReplicationThresholds(warning_lag_bytes=1, critical_lag_bytes=2)

    result = await service.assess(thresholds=thresholds, limit=10)

    assert result.status == "ready"
    repository.capture.assert_awaited_once_with(limit=10)


@pytest.mark.parametrize("value", [0, 101, True, 1.5, "5"])
def test_checked_limit_rejects_invalid_values(value) -> None:
    with pytest.raises(ReplicationValidationError, match="limit"):
        ReplicationService.checked_limit(value)


def test_checked_limit_accepts_boundaries() -> None:
    assert ReplicationService.checked_limit(1) == 1
    assert ReplicationService.checked_limit(100) == 100
