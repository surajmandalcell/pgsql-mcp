"""PostgreSQL-adapter contracts for replication diagnostics."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from typing import Any
from typing import cast
from unittest.mock import AsyncMock

import pytest
from psycopg import errors as pg_errors

from postgres_mcp.replication import NodeRole
from postgres_mcp.replication import PostgresReplicationRepository
from postgres_mcp.replication import ReplicationExecutionError
from postgres_mcp.replication import ReplicationTopology
from postgres_mcp.replication.postgres import _mapping
from postgres_mcp.replication.postgres import _optional_int
from postgres_mcp.replication.postgres import _optional_text
from postgres_mcp.replication.postgres import _seconds
from postgres_mcp.replication.postgres import topology_from_rows
from postgres_mcp.sql import SqlDriver

NOW = datetime(2026, 7, 26, tzinfo=timezone.utc)


class FakeCursor:
    def __init__(
        self,
        *,
        one: Any = None,
        rows: list[Any] | None = None,
        execute_error: BaseException | None = None,
    ) -> None:
        self.one = one
        self.rows = rows or []
        self.execute = AsyncMock(side_effect=execute_error)

    async def __aenter__(self) -> FakeCursor:
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        return None

    async def fetchone(self) -> Any:
        return self.one

    async def fetchall(self) -> list[Any]:
        return self.rows


class FakeConnection:
    def __init__(self, cursor: FakeCursor, *, rollback_error: BaseException | None = None) -> None:
        self._cursor = cursor
        self.rollback = AsyncMock(side_effect=rollback_error)

    def cursor(self, **_kwargs: Any) -> FakeCursor:
        return self._cursor


class FakeDriver:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection_value = connection

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[FakeConnection]:
        yield self.connection_value


def repository(connection: FakeConnection, *, timeout_seconds: float = 30) -> PostgresReplicationRepository:
    return PostgresReplicationRepository(cast(SqlDriver, FakeDriver(connection)), timeout_seconds=timeout_seconds)


def metadata(*, standby: bool = False) -> dict[str, Any]:
    return {
        "server_version_num": 180001,
        "database": "app",
        "current_user": "monitor",
        "in_recovery": standby,
        "transaction_read_only": standby,
        "wal_level": "replica",
        "max_wal_senders": 10,
        "max_replication_slots": 10,
        "hot_standby": True,
        "synchronous_standby_names_configured": False,
        "replay_paused": False,
        "current_wal_lsn": None if standby else "0/200",
        "received_wal_lsn": "0/200" if standby else None,
        "replayed_wal_lsn": "0/1F0" if standby else None,
        "replay_lag_bytes": 16 if standby else None,
    }


def standby_row() -> dict[str, Any]:
    return {
        "application_name": "standby-a",
        "client_address": "10.0.0.2",
        "state": "streaming",
        "sync_state": "async",
        "backend_start": NOW,
        "sent_lsn": "0/200",
        "write_lsn": "0/200",
        "flush_lsn": "0/200",
        "replay_lsn": "0/1F0",
        "write_lag": timedelta(milliseconds=10),
        "flush_lag": 0.02,
        "replay_lag": "0.03",
        "replay_lag_bytes": 16,
    }


def slot_row() -> dict[str, Any]:
    return {
        "slot_name": "slot_a",
        "slot_type": "physical",
        "database": None,
        "plugin": None,
        "active": False,
        "active_pid": None,
        "temporary": False,
        "restart_lsn": "0/100",
        "confirmed_flush_lsn": None,
        "retained_wal_bytes": 256,
        "wal_status": "reserved",
        "safe_wal_size_bytes": 2048,
        "conflicting": None,
    }


def receiver_row() -> dict[str, Any]:
    return {
        "status": "streaming",
        "slot_name": "slot_a",
        "sender_host": "primary.internal",
        "sender_port": 5432,
        "received_lsn": "0/200",
        "latest_end_lsn": "0/200",
        "last_msg_send_time": NOW,
        "last_msg_receipt_time": NOW,
        "latest_end_time": NOW,
    }


def subscription_row() -> dict[str, Any]:
    return {
        "subscription_name": "events_sub",
        "worker_pid": 777,
        "relation_oid": None,
        "received_lsn": "0/200",
        "latest_end_lsn": "0/200",
        "last_msg_send_time": NOW,
        "last_msg_receipt_time": NOW,
        "latest_end_time": NOW,
    }


def publication_row() -> dict[str, Any]:
    return {
        "name": "events_pub",
        "all_tables": False,
        "publish_insert": True,
        "publish_update": True,
        "publish_delete": True,
        "publish_truncate": True,
        "publish_via_partition_root": False,
    }


def test_metadata_query_guards_recovery_only_functions() -> None:
    from postgres_mcp.replication.postgres import _METADATA_SQL

    normalized = " ".join(_METADATA_SQL.split())
    assert "WHEN pg_catalog.pg_is_in_recovery() THEN pg_catalog.pg_is_wal_replay_paused()" in normalized
    assert "WHEN pg_catalog.pg_is_in_recovery() THEN pg_catalog.pg_last_wal_receive_lsn()::text" in normalized
    assert "WHEN pg_catalog.pg_is_in_recovery() THEN pg_catalog.pg_last_wal_replay_lsn()::text" in normalized


def test_row_conversion_builds_complete_topology() -> None:
    result = topology_from_rows(
        metadata(standby=True),
        standby_rows=[standby_row()],
        slot_rows=[slot_row()],
        receiver_row=receiver_row(),
        subscription_rows=[subscription_row()],
        publication_rows=[publication_row()],
        unavailable=("logical_origins",),
        captured_at=NOW,
    )

    assert result.role is NodeRole.STANDBY
    assert result.replay_lag_bytes == 16
    assert result.standbys[0].write_lag_seconds == 0.01
    assert result.standbys[0].flush_lag_seconds == 0.02
    assert result.standbys[0].replay_lag_seconds == 0.03
    assert result.slots[0].conflicting is None
    assert result.wal_receiver is not None
    assert result.wal_receiver.sender_host == "primary.internal"
    assert result.subscriptions[0].active is True
    assert result.publications[0].publish_delete is True
    assert result.unavailable == ("logical_origins",)


def test_row_conversion_handles_empty_optional_catalogs_and_current_timestamp() -> None:
    result = topology_from_rows(
        metadata(),
        standby_rows=[],
        slot_rows=[],
        receiver_row=None,
        subscription_rows=[],
        publication_rows=[],
    )

    assert result.role is NodeRole.PRIMARY
    assert result.wal_receiver is None
    assert result.captured_at.tzinfo is not None


def test_conversion_helpers_are_defensive() -> None:
    assert _mapping({"value": 1}) == {"value": 1}
    assert _mapping(("not", "mapping")) == {}
    assert _optional_text(None) is None
    assert _optional_text(12) == "12"
    assert _optional_int(None) is None
    assert _optional_int("12") == 12
    assert _seconds(None) is None
    assert _seconds(timedelta(seconds=-1)) == 0.0
    assert _seconds("1.5") == 1.5


def test_constructor_rejects_non_positive_timeout() -> None:
    with pytest.raises(ValueError, match="greater than zero"):
        PostgresReplicationRepository(cast(SqlDriver, object()), timeout_seconds=0)


@pytest.mark.asyncio
async def test_rows_and_one_helpers_execute_and_map_results() -> None:
    cursor = FakeCursor(one={"value": 1}, rows=[{"value": 1}, ("ignored",)])

    assert await PostgresReplicationRepository._one(cursor, "SELECT 1") == {"value": 1}
    assert await PostgresReplicationRepository._rows(cursor, "SELECT 1", [1]) == [{"value": 1}, {}]
    assert cursor.execute.await_count == 2

    cursor.one = None
    with pytest.raises(ReplicationExecutionError, match="no replication metadata"):
        await PostgresReplicationRepository._one(cursor, "SELECT 1")


@pytest.mark.asyncio
async def test_optional_catalog_permission_failure_is_reported_as_visibility_gap() -> None:
    cursor = FakeCursor()

    async def execute(sql: str, _params: list[Any] | None = None) -> None:
        if sql == "SELECT 1":
            raise pg_errors.InsufficientPrivilege("denied")

    cursor.execute = AsyncMock(side_effect=execute)
    adapter = repository(FakeConnection(cursor))
    unavailable: list[str] = []

    assert await adapter._optional_rows(cursor, "SELECT 1", None, capability="slots", unavailable=unavailable) == []
    assert unavailable == ["slots"]
    assert [call.args[0] for call in cursor.execute.await_args_list] == [
        "SAVEPOINT pgsql_mcp_replication_optional",
        "SELECT 1",
        "ROLLBACK TO SAVEPOINT pgsql_mcp_replication_optional",
        "RELEASE SAVEPOINT pgsql_mcp_replication_optional",
    ]


@pytest.mark.asyncio
async def test_optional_catalog_success_returns_rows() -> None:
    cursor = FakeCursor(rows=[{"slot_name": "slot_a"}])
    adapter = repository(FakeConnection(cursor))
    unavailable: list[str] = []

    rows = await adapter._optional_rows(cursor, "SELECT 1", [5], capability="slots", unavailable=unavailable)

    assert rows == [{"slot_name": "slot_a"}]
    assert unavailable == []
    assert cursor.execute.await_args_list[-1].args[0] == "RELEASE SAVEPOINT pgsql_mcp_replication_optional"


@pytest.mark.asyncio
async def test_capture_uses_one_read_only_transaction_and_rolls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    cursor = FakeCursor()
    connection = FakeConnection(cursor)
    adapter = repository(connection)
    monkeypatch.setattr(adapter, "_one", AsyncMock(return_value=metadata()))
    optional = AsyncMock(side_effect=[[standby_row()], [slot_row()], [], [subscription_row()], [publication_row()]])
    monkeypatch.setattr(adapter, "_optional_rows", optional)

    result = await adapter.capture(limit=25)

    assert isinstance(result, ReplicationTopology)
    assert result.standbys[0].application_name == "standby-a"
    assert cursor.execute.await_count == 5
    assert cursor.execute.await_args_list[0].args[0] == "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY"
    assert optional.await_count == 5
    connection.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_capture_rejects_unconfirmed_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    cursor = FakeCursor()
    connection = FakeConnection(cursor, rollback_error=RuntimeError("lost"))
    adapter = repository(connection)
    monkeypatch.setattr(adapter, "_one", AsyncMock(return_value=metadata()))
    monkeypatch.setattr(adapter, "_optional_rows", AsyncMock(side_effect=[[], [], [], [], []]))

    with pytest.raises(ReplicationExecutionError, match="cleanup could not be confirmed"):
        await adapter.capture(limit=5)


@pytest.mark.asyncio
async def test_capture_wraps_database_failure_and_rolls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    cursor = FakeCursor()
    connection = FakeConnection(cursor)
    adapter = repository(connection)
    monkeypatch.setattr(adapter, "_one", AsyncMock(side_effect=RuntimeError("catalog failed")))

    with pytest.raises(ReplicationExecutionError, match="snapshot failed"):
        await adapter.capture(limit=5)

    connection.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_capture_preserves_domain_failure_and_reports_rollback_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    cursor = FakeCursor()
    connection = FakeConnection(cursor, rollback_error=RuntimeError("lost"))
    adapter = repository(connection)
    monkeypatch.setattr(adapter, "_one", AsyncMock(side_effect=ReplicationExecutionError("bad metadata")))

    with pytest.raises(ReplicationExecutionError, match="rollback could not be confirmed"):
        await adapter.capture(limit=5)


@pytest.mark.asyncio
async def test_capture_cancellation_attempts_rollback(monkeypatch: pytest.MonkeyPatch) -> None:
    cursor = FakeCursor()
    connection = FakeConnection(cursor)
    adapter = repository(connection)
    monkeypatch.setattr(adapter, "_one", AsyncMock(side_effect=asyncio.CancelledError()))

    with pytest.raises(asyncio.CancelledError):
        await adapter.capture(limit=5)

    connection.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_capture_timeout_has_stable_error(monkeypatch: pytest.MonkeyPatch) -> None:
    cursor = FakeCursor()
    adapter = repository(FakeConnection(cursor), timeout_seconds=0.001)

    async def block(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        await asyncio.sleep(1)
        return metadata()

    monkeypatch.setattr(adapter, "_one", block)

    with pytest.raises(ReplicationExecutionError, match="timed out"):
        await adapter.capture(limit=5)


@pytest.mark.asyncio
async def test_non_exception_base_error_is_not_wrapped(monkeypatch: pytest.MonkeyPatch) -> None:
    class Stop(BaseException):
        pass

    cursor = FakeCursor()
    adapter = repository(FakeConnection(cursor))
    monkeypatch.setattr(adapter, "_one", AsyncMock(side_effect=Stop()))

    with pytest.raises(Stop):
        await adapter.capture(limit=5)
