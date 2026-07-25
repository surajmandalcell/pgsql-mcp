"""Application service for reviewed nontransactional maintenance."""

from __future__ import annotations

import hmac
from typing import Protocol

from .domain import MaintenanceOperationResult
from .domain import MaintenancePlan
from .domain import MaintenancePlanner
from .domain import MaintenanceRequest
from .domain import MaintenanceReviewMismatch
from .domain import MaintenanceStatusSnapshot
from .domain import ReconciliationResolution
from .domain import TargetSnapshot
from .domain import _checked_name
from .domain import _checked_review_hash


class MaintenanceBackend(Protocol):
    """Infrastructure boundary for maintenance planning and execution."""

    async def inspect(self, request: MaintenanceRequest) -> TargetSnapshot:
        """Resolve the live PostgreSQL target identity and preconditions."""

    async def apply(
        self,
        plan: MaintenancePlan,
        *,
        timeout_seconds: int,
        lock_timeout_seconds: int,
    ) -> MaintenanceOperationResult:
        """Execute one reviewed maintenance plan outside a transaction block."""

    async def status(self, *, limit: int) -> MaintenanceStatusSnapshot:
        """Return bounded redacted durable maintenance history."""

    async def reconcile(
        self,
        *,
        name: str,
        review_hash: str,
        resolution: ReconciliationResolution,
    ) -> MaintenanceOperationResult:
        """Resolve an unknown outcome after external operator verification."""


class MaintenanceService:
    """Verify reviewed aggregates before delegating to PostgreSQL."""

    def __init__(
        self,
        backend: MaintenanceBackend,
        *,
        planner: MaintenancePlanner | None = None,
    ) -> None:
        self.backend = backend
        self.planner = planner or MaintenancePlanner()

    async def plan(self, request: MaintenanceRequest) -> MaintenancePlan:
        snapshot = await self.backend.inspect(request)
        return self.planner.create_plan(request, snapshot)

    async def apply(
        self,
        plan: MaintenancePlan,
        *,
        review_hash: str,
        timeout_seconds: int,
        lock_timeout_seconds: int,
    ) -> MaintenanceOperationResult:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        if lock_timeout_seconds <= 0:
            raise ValueError("lock_timeout_seconds must be greater than zero")
        plan.assert_integrity()
        supplied_hash = _checked_review_hash(review_hash)
        if not hmac.compare_digest(plan.review_hash, supplied_hash):
            raise MaintenanceReviewMismatch("supplied review_hash does not match the reviewed maintenance plan")
        return await self.backend.apply(
            plan,
            timeout_seconds=timeout_seconds,
            lock_timeout_seconds=lock_timeout_seconds,
        )

    async def status(self, *, limit: int = 100) -> MaintenanceStatusSnapshot:
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1 or limit > 500:
            raise ValueError("maintenance status limit must be between 1 and 500")
        return await self.backend.status(limit=limit)

    async def reconcile(
        self,
        *,
        name: str,
        review_hash: str,
        resolution: ReconciliationResolution,
    ) -> MaintenanceOperationResult:
        checked_name = _checked_name(name)
        checked_hash = _checked_review_hash(review_hash)
        checked_resolution = ReconciliationResolution(resolution)
        return await self.backend.reconcile(
            name=checked_name,
            review_hash=checked_hash,
            resolution=checked_resolution,
        )
