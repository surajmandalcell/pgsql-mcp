"""Application-service contracts for replication diagnostics."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from postgres_mcp.replication import ReplicationRepository
from postgres_mcp.replication import ReplicationRole
from postgres_mcp.replication import ReplicationService
from postgres_mcp.replication import ReplicationSettings
from postgres_mcp.replication import ReplicationSnapshot
from postgres_mcp.replication import ReplicationThresholds
from postgres_mcp.replication import ReplicationValidationError


def snapshot() -> ReplicationSnapshot:
    return ReplicationSnapshot(
        settings=ReplicationSettings(
            server_version_num=180000,
            database="app",
            current_user="monitor",
            role=ReplicationRole.PRIMARY,
            wal_level="replica",
            max_wal_senders=10,
            max_replication_slots=10,
            hot_standby=True,
            archive_mode="off",
            synchronous_standby_names="",
            current_wal_lsn="0/1",
            replay_paused=False,
            captured_at=None,
        )
    )


@pytest.mark.asyncio
async def test_service_applies_default_policy_to_a_bounded_snapshot() -> None:
    repository = AsyncMock(spec=ReplicationRepository)
    repository.load_snapshot.return_value = snapshot()
    service = ReplicationService(repository)

    result = await service.snapshot()

    assert result.warnings == ()
    repository.load_snapshot.assert_awaited_once_with(limit=100)


@pytest.mark.asyncio
async def test_service_forwards_custom_limit_and_thresholds() -> None:
    repository = AsyncMock(spec=ReplicationRepository)
    repository.load_snapshot.return_value = snapshot()
    service = ReplicationService(repository)
    thresholds = ReplicationThresholds(warning_lag_seconds=1, critical_lag_seconds=2)

    await service.snapshot(limit=25, thresholds=thresholds)

    repository.load_snapshot.assert_awaited_once_with(limit=25)


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [True, 0, -1, 501])
async def test_service_rejects_invalid_limits_without_repository_access(limit: int) -> None:
    repository = AsyncMock(spec=ReplicationRepository)
    service = ReplicationService(repository)

    with pytest.raises(ReplicationValidationError):
        await service.snapshot(limit=limit)

    repository.load_snapshot.assert_not_awaited()
