"""Application service for read-only replication diagnostics."""

from __future__ import annotations

from typing import Protocol

from .domain import MAX_REPLICATION_ROWS
from .domain import ReplicationSnapshot
from .domain import ReplicationThresholds
from .domain import ReplicationValidationError
from .domain import evaluate_replication_health


class ReplicationRepository(Protocol):
    """Port implemented by a PostgreSQL replication catalog adapter."""

    async def load_snapshot(self, *, limit: int) -> ReplicationSnapshot: ...


class ReplicationService:
    """Coordinate bounded repository reads and deterministic health policy."""

    def __init__(self, repository: ReplicationRepository):
        self._repository = repository

    async def snapshot(
        self,
        *,
        limit: int = 100,
        thresholds: ReplicationThresholds | None = None,
    ) -> ReplicationSnapshot:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise ReplicationValidationError("limit must be a positive integer")
        if limit > MAX_REPLICATION_ROWS:
            raise ReplicationValidationError(f"limit cannot exceed {MAX_REPLICATION_ROWS}")
        selected_thresholds = thresholds or ReplicationThresholds()
        snapshot = await self._repository.load_snapshot(limit=limit)
        return snapshot.with_warnings(evaluate_replication_health(snapshot, selected_thresholds))
