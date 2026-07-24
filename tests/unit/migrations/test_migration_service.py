"""Application-service contracts for reviewed migration execution."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from postgres_mcp.migrations.domain import MigrationNotApplyable
from postgres_mcp.migrations.domain import MigrationOperationResult
from postgres_mcp.migrations.domain import MigrationOperationStatus
from postgres_mcp.migrations.domain import MigrationReviewMismatch
from postgres_mcp.migrations.domain import MigrationStatusSnapshot
from postgres_mcp.migrations.domain import MigrationStepDraft
from postgres_mcp.migrations.domain import MigrationValidationError
from postgres_mcp.migrations.planner import MigrationPlanner
from postgres_mcp.migrations.service import MigrationBackend
from postgres_mcp.migrations.service import MigrationService


def plan():
    return MigrationPlanner().create_plan(
        name="create-items",
        steps=[MigrationStepDraft("CREATE TABLE app.items(id integer)", "DROP TABLE app.items")],
    )


@pytest.mark.asyncio
async def test_apply_requires_exact_review_hash_before_backend_call() -> None:
    backend = AsyncMock(spec=MigrationBackend)
    service = MigrationService(backend)
    migration = plan()

    with pytest.raises(MigrationReviewMismatch, match="does not match"):
        await service.apply(
            migration,
            review_hash="0" * 64,
            timeout_seconds=30,
            lock_timeout_seconds=5,
        )

    backend.apply.assert_not_awaited()


@pytest.mark.asyncio
async def test_apply_rejects_tampered_aggregate_before_review_or_database() -> None:
    backend = AsyncMock(spec=MigrationBackend)
    service = MigrationService(backend)
    migration = replace(plan(), checksum="0" * 64)

    with pytest.raises(MigrationValidationError, match="checksum"):
        await service.apply(
            migration,
            review_hash=migration.review_hash,
            timeout_seconds=30,
            lock_timeout_seconds=5,
        )

    backend.apply.assert_not_awaited()


@pytest.mark.asyncio
async def test_apply_rejects_nontransactional_plan_before_backend_call() -> None:
    backend = AsyncMock(spec=MigrationBackend)
    service = MigrationService(backend)
    migration = MigrationPlanner().create_plan(
        name="concurrent-index",
        steps=[
            MigrationStepDraft(
                "CREATE INDEX CONCURRENTLY items_id_idx ON app.items(id)",
                "DROP INDEX CONCURRENTLY app.items_id_idx",
            )
        ],
    )

    with pytest.raises(MigrationNotApplyable, match="not fully transactional"):
        await service.apply(
            migration,
            review_hash=migration.review_hash,
            timeout_seconds=30,
            lock_timeout_seconds=5,
        )

    backend.apply.assert_not_awaited()


@pytest.mark.asyncio
async def test_apply_forwards_only_reviewed_transactional_plan() -> None:
    backend = AsyncMock(spec=MigrationBackend)
    backend.apply.return_value = MigrationOperationResult(MigrationOperationStatus.APPLIED, None)
    service = MigrationService(backend)
    migration = plan()

    result = await service.apply(
        migration,
        review_hash=migration.review_hash.upper(),
        timeout_seconds=45,
        lock_timeout_seconds=3,
    )

    assert result.status is MigrationOperationStatus.APPLIED
    backend.apply.assert_awaited_once_with(migration, timeout_seconds=45, lock_timeout_seconds=3)


@pytest.mark.asyncio
async def test_timeouts_are_bounded_before_backend_call() -> None:
    backend = AsyncMock(spec=MigrationBackend)
    service = MigrationService(backend)
    migration = plan()

    for timeout_seconds, lock_timeout_seconds, message in [
        (0, 1, "timeout_seconds"),
        (901, 1, "timeout_seconds"),
        (30, 0, "lock_timeout_seconds"),
        (30, 301, "lock_timeout_seconds"),
        (10, 11, "cannot exceed"),
    ]:
        with pytest.raises(ValueError, match=message):
            await service.apply(
                migration,
                review_hash=migration.review_hash,
                timeout_seconds=timeout_seconds,
                lock_timeout_seconds=lock_timeout_seconds,
            )
    backend.apply.assert_not_awaited()


@pytest.mark.asyncio
async def test_rollback_and_status_validate_inputs() -> None:
    backend = AsyncMock(spec=MigrationBackend)
    backend.rollback.return_value = MigrationOperationResult(MigrationOperationStatus.ROLLED_BACK, None)
    backend.status.return_value = MigrationStatusSnapshot(())
    service = MigrationService(backend)

    with pytest.raises(MigrationReviewMismatch, match="64-character"):
        await service.rollback(name="create-items", review_hash="bad", timeout_seconds=30, lock_timeout_seconds=5)
    with pytest.raises(MigrationValidationError, match="may contain only"):
        await service.rollback(name="bad name", review_hash="a" * 64, timeout_seconds=30, lock_timeout_seconds=5)
    backend.rollback.assert_not_awaited()

    await service.rollback(name=" create-items ", review_hash="A" * 64, timeout_seconds=30, lock_timeout_seconds=5)
    backend.rollback.assert_awaited_once_with(
        name="create-items",
        review_hash="a" * 64,
        timeout_seconds=30,
        lock_timeout_seconds=5,
    )

    with pytest.raises(ValueError, match="cannot exceed 500"):
        await service.status(limit=501)
    assert await service.status(limit=10) == MigrationStatusSnapshot(())
