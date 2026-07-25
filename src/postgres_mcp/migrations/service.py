"""Application service for reviewed PostgreSQL migrations."""

from __future__ import annotations

from typing import Protocol

from .domain import MigrationNotApplyable
from .domain import MigrationOperationResult
from .domain import MigrationPlan
from .domain import MigrationReviewMismatch
from .domain import MigrationStatusSnapshot
from .domain import normalize_review_hash
from .domain import validate_migration_name

MAX_STATUS_ROWS = 500
MAX_TIMEOUT_SECONDS = 900
MAX_LOCK_TIMEOUT_SECONDS = 300


class MigrationBackend(Protocol):
    """Port implemented by a database-specific migration adapter."""

    async def apply(
        self,
        plan: MigrationPlan,
        *,
        timeout_seconds: int,
        lock_timeout_seconds: int,
    ) -> MigrationOperationResult: ...

    async def rollback(
        self,
        *,
        name: str,
        review_hash: str,
        timeout_seconds: int,
        lock_timeout_seconds: int,
    ) -> MigrationOperationResult: ...

    async def status(self, *, limit: int) -> MigrationStatusSnapshot: ...


class MigrationService:
    """Coordinate review verification and backend use cases."""

    def __init__(self, backend: MigrationBackend):
        self.backend = backend

    async def apply(
        self,
        plan: MigrationPlan,
        *,
        review_hash: str,
        timeout_seconds: int,
        lock_timeout_seconds: int,
    ) -> MigrationOperationResult:
        plan.assert_integrity()
        normalized_hash = normalize_review_hash(review_hash)
        if normalized_hash != plan.review_hash:
            raise MigrationReviewMismatch("supplied review_hash does not match the reviewed migration plan")
        if not plan.applyable:
            raise MigrationNotApplyable("migration plan is not fully transactional and cannot use the atomic executor")
        _validate_timeouts(timeout_seconds, lock_timeout_seconds)
        return await self.backend.apply(
            plan,
            timeout_seconds=timeout_seconds,
            lock_timeout_seconds=lock_timeout_seconds,
        )

    async def rollback(
        self,
        *,
        name: str,
        review_hash: str,
        timeout_seconds: int,
        lock_timeout_seconds: int,
    ) -> MigrationOperationResult:
        normalized_name = validate_migration_name(name)
        normalized_hash = normalize_review_hash(review_hash)
        _validate_timeouts(timeout_seconds, lock_timeout_seconds)
        return await self.backend.rollback(
            name=normalized_name,
            review_hash=normalized_hash,
            timeout_seconds=timeout_seconds,
            lock_timeout_seconds=lock_timeout_seconds,
        )

    async def status(self, *, limit: int = 100) -> MigrationStatusSnapshot:
        if limit <= 0:
            raise ValueError("limit must be greater than zero")
        if limit > MAX_STATUS_ROWS:
            raise ValueError(f"limit cannot exceed {MAX_STATUS_ROWS}")
        return await self.backend.status(limit=limit)


def _validate_timeouts(timeout_seconds: int, lock_timeout_seconds: int) -> None:
    if timeout_seconds <= 0 or timeout_seconds > MAX_TIMEOUT_SECONDS:
        raise ValueError(f"timeout_seconds must be between 1 and {MAX_TIMEOUT_SECONDS}")
    if lock_timeout_seconds <= 0 or lock_timeout_seconds > MAX_LOCK_TIMEOUT_SECONDS:
        raise ValueError(f"lock_timeout_seconds must be between 1 and {MAX_LOCK_TIMEOUT_SECONDS}")
    if lock_timeout_seconds > timeout_seconds:
        raise ValueError("lock_timeout_seconds cannot exceed timeout_seconds")
