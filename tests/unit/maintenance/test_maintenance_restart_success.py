"""Positive persistence contract for restarting a failed maintenance record."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from typing import cast
from unittest.mock import AsyncMock

import pytest

from postgres_mcp.maintenance import MaintenanceOperation
from postgres_mcp.maintenance import MaintenancePlanner
from postgres_mcp.maintenance import MaintenanceRequest
from postgres_mcp.maintenance import MaintenanceTarget
from postgres_mcp.maintenance import PostgresMaintenanceBackend
from postgres_mcp.maintenance import TargetSnapshot
from postgres_mcp.sql import SqlDriver


class Cursor:
    def __init__(self, row: dict[str, Any]) -> None:
        self.row = row
        self.execute = AsyncMock()

    async def __aenter__(self) -> Cursor:
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        return None

    async def fetchone(self) -> dict[str, Any]:
        return self.row


class Connection:
    def __init__(self, row: dict[str, Any]) -> None:
        self._cursor = Cursor(row)

    def cursor(self, **_kwargs: Any) -> Cursor:
        return self._cursor


class Driver:
    def __init__(self, connection: Connection) -> None:
        self.connection_value = connection
        self.conn = object()

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[Connection]:
        yield self.connection_value


def reviewed_plan():
    return MaintenancePlanner().create_plan(
        MaintenanceRequest(
            name="retry-maintenance",
            operation=MaintenanceOperation.ANALYZE,
            target=MaintenanceTarget("app", "items"),
        ),
        TargetSnapshot(
            oid=42,
            relation_kind="r",
            persistence="p",
            is_partition=False,
            is_populated=True,
            has_usable_unique_index=False,
            is_exclusion_index=False,
        ),
    )


@pytest.mark.asyncio
async def test_restart_record_returns_the_persisted_mapping() -> None:
    plan = reviewed_plan()
    expected = {
        "id": 7,
        "name": plan.name,
        "review_hash": plan.review_hash,
    }
    connection = Connection(expected)
    adapter = PostgresMaintenanceBackend(cast(SqlDriver, Driver(connection)))

    result = await adapter._restart_record(connection, plan)

    assert result == expected
    connection._cursor.execute.assert_awaited_once()
