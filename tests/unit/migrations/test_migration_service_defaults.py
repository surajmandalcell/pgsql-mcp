"""Default-path contracts for the reviewed migration application service."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from postgres_mcp.migrations.domain import MigrationStatusSnapshot
from postgres_mcp.migrations.service import MigrationBackend
from postgres_mcp.migrations.service import MigrationService


@pytest.mark.asyncio
async def test_status_uses_the_bounded_default_limit() -> None:
    backend = AsyncMock(spec=MigrationBackend)
    backend.status.return_value = MigrationStatusSnapshot(())
    service = MigrationService(backend)

    assert await service.status() == MigrationStatusSnapshot(())
    backend.status.assert_awaited_once_with(limit=100)


@pytest.mark.asyncio
async def test_status_rejects_nonpositive_limit_without_backend_access() -> None:
    backend = AsyncMock(spec=MigrationBackend)
    service = MigrationService(backend)

    with pytest.raises(ValueError, match="greater than zero"):
        await service.status(limit=0)

    backend.status.assert_not_awaited()
