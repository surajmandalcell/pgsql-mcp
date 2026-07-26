"""Application service for replication topology and failover readiness."""

from __future__ import annotations

from typing import Protocol

from .domain import MAX_REPLICATION_ROWS
from .domain import FailoverReadinessAssessment
from .domain import ReplicationThresholds
from .domain import ReplicationTopology
from .domain import ReplicationValidationError
from .domain import assess_failover_readiness


class ReplicationRepository(Protocol):
    """Infrastructure port owned by the replication bounded context."""

    async def capture(self, *, limit: int) -> ReplicationTopology: ...


class ReplicationService:
    """Use-case boundary for bounded, read-only replication diagnostics."""

    def __init__(self, repository: ReplicationRepository):
        self._repository = repository

    @staticmethod
    def checked_limit(limit: int) -> int:
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise ReplicationValidationError("limit must be an integer")
        if limit < 1 or limit > MAX_REPLICATION_ROWS:
            raise ReplicationValidationError(f"limit must be between 1 and {MAX_REPLICATION_ROWS}")
        return limit

    async def topology(self, *, limit: int = 50) -> ReplicationTopology:
        return await self._repository.capture(limit=self.checked_limit(limit))

    async def assess(
        self,
        *,
        thresholds: ReplicationThresholds | None = None,
        limit: int = 50,
    ) -> FailoverReadinessAssessment:
        topology = await self.topology(limit=limit)
        return assess_failover_readiness(topology, thresholds)
