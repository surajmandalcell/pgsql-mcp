"""Application-service contracts for reviewed maintenance workflows."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from postgres_mcp.maintenance import MaintenanceBackend
from postgres_mcp.maintenance import MaintenanceOperation
from postgres_mcp.maintenance import MaintenanceOperationResult
from postgres_mcp.maintenance import MaintenanceOperationStatus
from postgres_mcp.maintenance import MaintenancePlanner
from postgres_mcp.maintenance import MaintenanceRequest
from postgres_mcp.maintenance import MaintenanceReviewMismatch
from postgres_mcp.maintenance import MaintenanceService
from postgres_mcp.maintenance import MaintenanceStatusSnapshot
from postgres_mcp.maintenance import MaintenanceTarget
from postgres_mcp.maintenance import ReconciliationResolution
from postgres_mcp.maintenance import TargetSnapshot


def request() -> MaintenanceRequest:
    return MaintenanceRequest(
        name="nightly-items-maintenance",
        operation=MaintenanceOperation.VACUUM_ANALYZE,
        target=MaintenanceTarget("app", "items"),
    )


def snapshot() -> TargetSnapshot:
    return TargetSnapshot(
        oid=42,
        relation_kind="r",
        is_partition=False,
        has_usable_unique_index=False,
    )


@pytest.mark.asyncio
async def test_plan_combines_live_inspection_with_the_pure_planner() -> None:
    backend = AsyncMock(spec=MaintenanceBackend)
    backend.inspect.return_value = snapshot()
    service = MaintenanceService(backend, planner=MaintenancePlanner())

    plan = await service.plan(request())

    backend.inspect.assert_awaited_once_with(request())
    assert plan.target_oid == 42


@pytest.mark.asyncio
async def test_apply_rejects_review_mismatch_before_backend_access() -> None:
    backend = AsyncMock(spec=MaintenanceBackend)
    service = MaintenanceService(backend)
    plan = MaintenancePlanner().create_plan(request(), snapshot())

    with pytest.raises(MaintenanceReviewMismatch, match="review_hash"):
        await service.apply(
            plan,
            review_hash="0" * 64,
            timeout_seconds=30,
            lock_timeout_seconds=5,
        )

    backend.apply.assert_not_awaited()


@pytest.mark.asyncio
async def test_apply_delegates_an_integrity_checked_plan() -> None:
    backend = AsyncMock(spec=MaintenanceBackend)
    service = MaintenanceService(backend)
    plan = MaintenancePlanner().create_plan(request(), snapshot())
    expected = MaintenanceOperationResult(MaintenanceOperationStatus.SUCCEEDED, None)
    backend.apply.return_value = expected

    result = await service.apply(
        plan,
        review_hash=plan.review_hash,
        timeout_seconds=30,
        lock_timeout_seconds=5,
    )

    assert result is expected
    backend.apply.assert_awaited_once_with(
        plan,
        timeout_seconds=30,
        lock_timeout_seconds=5,
    )


@pytest.mark.asyncio
async def test_status_is_bounded_and_uses_a_safe_default() -> None:
    backend = AsyncMock(spec=MaintenanceBackend)
    backend.status.return_value = MaintenanceStatusSnapshot(())
    service = MaintenanceService(backend)

    assert await service.status() == MaintenanceStatusSnapshot(())
    backend.status.assert_awaited_once_with(limit=100)

    with pytest.raises(ValueError, match="between 1 and 500"):
        await service.status(limit=0)


@pytest.mark.asyncio
async def test_reconciliation_requires_exact_hash_and_explicit_resolution() -> None:
    backend = AsyncMock(spec=MaintenanceBackend)
    service = MaintenanceService(backend)
    expected = MaintenanceOperationResult(MaintenanceOperationStatus.FAILED, None)
    backend.reconcile.return_value = expected

    result = await service.reconcile(
        name="nightly-items-maintenance",
        review_hash="a" * 64,
        resolution=ReconciliationResolution.FAILED,
    )

    assert result is expected
    backend.reconcile.assert_awaited_once_with(
        name="nightly-items-maintenance",
        review_hash="a" * 64,
        resolution=ReconciliationResolution.FAILED,
    )

    with pytest.raises(ValueError, match="64-character"):
        await service.reconcile(
            name="nightly-items-maintenance",
            review_hash="bad",
            resolution=ReconciliationResolution.SUCCEEDED,
        )
