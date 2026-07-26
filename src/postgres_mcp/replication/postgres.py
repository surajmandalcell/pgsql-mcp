"""PostgreSQL adapter for bounded, secret-free replication diagnostics."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from typing import Any

from psycopg import errors as pg_errors
from psycopg.rows import dict_row

from postgres_mcp.sql import SqlDriver

from .domain import LogicalSubscription
from .domain import NodeRole
from .domain import Publication
from .domain import ReplicationExecutionError
from .domain import ReplicationSlot
from .domain import ReplicationStandby
from .domain import ReplicationTopology
from .domain import WalReceiver

_OPTIONAL_ERRORS = (
    pg_errors.InsufficientPrivilege,
    pg_errors.UndefinedColumn,
    pg_errors.UndefinedTable,
    pg_errors.FeatureNotSupported,
)

_METADATA_SQL = """
SELECT
    current_setting('server_version_num')::integer AS server_version_num,
    current_database() AS database,
    current_user,
    pg_catalog.pg_is_in_recovery() AS in_recovery,
    current_setting('transaction_read_only') = 'on' AS transaction_read_only,
    current_setting('wal_level') AS wal_level,
    current_setting('max_wal_senders')::integer AS max_wal_senders,
    current_setting('max_replication_slots')::integer AS max_replication_slots,
    current_setting('hot_standby') = 'on' AS hot_standby,
    NULLIF(current_setting('synchronous_standby_names'), '') IS NOT NULL AS synchronous_standby_names_configured,
    CASE
        WHEN pg_catalog.pg_is_in_recovery() THEN pg_catalog.pg_is_wal_replay_paused()
        ELSE false
    END AS replay_paused,
    CASE WHEN pg_catalog.pg_is_in_recovery() THEN NULL ELSE pg_catalog.pg_current_wal_lsn()::text END AS current_wal_lsn,
    CASE
        WHEN pg_catalog.pg_is_in_recovery() THEN pg_catalog.pg_last_wal_receive_lsn()::text
        ELSE NULL
    END AS received_wal_lsn,
    CASE
        WHEN pg_catalog.pg_is_in_recovery() THEN pg_catalog.pg_last_wal_replay_lsn()::text
        ELSE NULL
    END AS replayed_wal_lsn,
    CASE
        WHEN pg_catalog.pg_is_in_recovery()
         AND pg_catalog.pg_last_wal_receive_lsn() IS NOT NULL
         AND pg_catalog.pg_last_wal_replay_lsn() IS NOT NULL
        THEN GREATEST(
            pg_catalog.pg_wal_lsn_diff(pg_catalog.pg_last_wal_receive_lsn(), pg_catalog.pg_last_wal_replay_lsn()),
            0
        )::bigint
        ELSE NULL
    END AS replay_lag_bytes
"""

_STANDBYS_SQL = """
SELECT
    application_name,
    client_addr::text AS client_address,
    state,
    sync_state,
    backend_start,
    sent_lsn::text,
    write_lsn::text,
    flush_lsn::text,
    replay_lsn::text,
    write_lag,
    flush_lag,
    replay_lag,
    CASE
        WHEN replay_lsn IS NULL THEN NULL
        ELSE GREATEST(pg_catalog.pg_wal_lsn_diff(pg_catalog.pg_current_wal_lsn(), replay_lsn), 0)::bigint
    END AS replay_lag_bytes
FROM pg_catalog.pg_stat_replication
ORDER BY application_name, pid
LIMIT %s
"""

_SLOTS_SQL = """
SELECT
    slot_name,
    slot_type,
    database,
    plugin,
    active,
    active_pid,
    temporary,
    restart_lsn::text,
    confirmed_flush_lsn::text,
    CASE
        WHEN pg_catalog.pg_is_in_recovery() OR restart_lsn IS NULL THEN NULL
        ELSE GREATEST(pg_catalog.pg_wal_lsn_diff(pg_catalog.pg_current_wal_lsn(), restart_lsn), 0)::bigint
    END AS retained_wal_bytes,
    pg_catalog.to_jsonb(s)->>'wal_status' AS wal_status,
    NULLIF(pg_catalog.to_jsonb(s)->>'safe_wal_size', '')::bigint AS safe_wal_size_bytes,
    NULLIF(pg_catalog.to_jsonb(s)->>'conflicting', '')::boolean AS conflicting
FROM pg_catalog.pg_replication_slots AS s
ORDER BY slot_name
LIMIT %s
"""

_RECEIVER_SQL = """
SELECT
    status,
    pg_catalog.to_jsonb(w)->>'slot_name' AS slot_name,
    sender_host,
    sender_port,
    COALESCE(flushed_lsn, written_lsn)::text AS received_lsn,
    latest_end_lsn::text,
    last_msg_send_time,
    last_msg_receipt_time,
    latest_end_time
FROM pg_catalog.pg_stat_wal_receiver AS w
LIMIT 1
"""

_SUBSCRIPTIONS_SQL = """
SELECT
    sub.subname AS subscription_name,
    st.pid AS worker_pid,
    st.relid::bigint AS relation_oid,
    st.received_lsn::text,
    st.latest_end_lsn::text,
    st.last_msg_send_time,
    st.last_msg_receipt_time,
    st.latest_end_time
FROM pg_catalog.pg_subscription AS sub
LEFT JOIN pg_catalog.pg_stat_subscription AS st ON st.subid = sub.oid
ORDER BY sub.subname, st.relid NULLS FIRST
LIMIT %s
"""

_PUBLICATIONS_SQL = """
SELECT
    pubname AS name,
    puballtables AS all_tables,
    pubinsert AS publish_insert,
    pubupdate AS publish_update,
    pubdelete AS publish_delete,
    pubtruncate AS publish_truncate,
    pubviaroot AS publish_via_partition_root
FROM pg_catalog.pg_publication
ORDER BY pubname
LIMIT %s
"""


def _mapping(row: Any) -> Mapping[str, Any]:
    return row if isinstance(row, Mapping) else {}


def _optional_text(value: Any) -> str | None:
    return None if value is None else str(value)


def _optional_int(value: Any) -> int | None:
    return None if value is None else int(value)


def _seconds(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, timedelta):
        return max(0.0, value.total_seconds())
    return max(0.0, float(value))


def topology_from_rows(
    metadata: Mapping[str, Any],
    *,
    standby_rows: list[Mapping[str, Any]],
    slot_rows: list[Mapping[str, Any]],
    receiver_row: Mapping[str, Any] | None,
    subscription_rows: list[Mapping[str, Any]],
    publication_rows: list[Mapping[str, Any]],
    unavailable: tuple[str, ...] = (),
    captured_at: datetime | None = None,
) -> ReplicationTopology:
    """Convert trusted query rows into the immutable replication aggregate."""
    standbys = tuple(
        ReplicationStandby(
            application_name=str(row["application_name"]),
            client_address=_optional_text(row.get("client_address")),
            state=str(row["state"]),
            sync_state=str(row["sync_state"]),
            backend_start=row.get("backend_start"),
            sent_lsn=_optional_text(row.get("sent_lsn")),
            write_lsn=_optional_text(row.get("write_lsn")),
            flush_lsn=_optional_text(row.get("flush_lsn")),
            replay_lsn=_optional_text(row.get("replay_lsn")),
            write_lag_seconds=_seconds(row.get("write_lag")),
            flush_lag_seconds=_seconds(row.get("flush_lag")),
            replay_lag_seconds=_seconds(row.get("replay_lag")),
            replay_lag_bytes=_optional_int(row.get("replay_lag_bytes")),
        )
        for row in standby_rows
    )
    slots = tuple(
        ReplicationSlot(
            slot_name=str(row["slot_name"]),
            slot_type=str(row["slot_type"]),
            database=_optional_text(row.get("database")),
            plugin=_optional_text(row.get("plugin")),
            active=bool(row["active"]),
            active_pid=_optional_int(row.get("active_pid")),
            temporary=bool(row["temporary"]),
            restart_lsn=_optional_text(row.get("restart_lsn")),
            confirmed_flush_lsn=_optional_text(row.get("confirmed_flush_lsn")),
            retained_wal_bytes=_optional_int(row.get("retained_wal_bytes")),
            wal_status=_optional_text(row.get("wal_status")),
            safe_wal_size_bytes=_optional_int(row.get("safe_wal_size_bytes")),
            conflicting=None if row.get("conflicting") is None else bool(row["conflicting"]),
        )
        for row in slot_rows
    )
    receiver = (
        WalReceiver(
            status=str(receiver_row["status"]),
            slot_name=_optional_text(receiver_row.get("slot_name")),
            sender_host=_optional_text(receiver_row.get("sender_host")),
            sender_port=_optional_int(receiver_row.get("sender_port")),
            received_lsn=_optional_text(receiver_row.get("received_lsn")),
            latest_end_lsn=_optional_text(receiver_row.get("latest_end_lsn")),
            last_msg_send_time=receiver_row.get("last_msg_send_time"),
            last_msg_receipt_time=receiver_row.get("last_msg_receipt_time"),
            latest_end_time=receiver_row.get("latest_end_time"),
        )
        if receiver_row
        else None
    )
    subscriptions = tuple(
        LogicalSubscription(
            subscription_name=str(row["subscription_name"]),
            worker_pid=_optional_int(row.get("worker_pid")),
            relation_oid=_optional_int(row.get("relation_oid")),
            received_lsn=_optional_text(row.get("received_lsn")),
            latest_end_lsn=_optional_text(row.get("latest_end_lsn")),
            last_msg_send_time=row.get("last_msg_send_time"),
            last_msg_receipt_time=row.get("last_msg_receipt_time"),
            latest_end_time=row.get("latest_end_time"),
        )
        for row in subscription_rows
    )
    publications = tuple(
        Publication(
            name=str(row["name"]),
            all_tables=bool(row["all_tables"]),
            publish_insert=bool(row["publish_insert"]),
            publish_update=bool(row["publish_update"]),
            publish_delete=bool(row["publish_delete"]),
            publish_truncate=bool(row["publish_truncate"]),
            publish_via_partition_root=bool(row["publish_via_partition_root"]),
        )
        for row in publication_rows
    )
    return ReplicationTopology(
        captured_at=captured_at or datetime.now(timezone.utc),
        server_version_num=int(metadata["server_version_num"]),
        database=str(metadata["database"]),
        current_user=str(metadata["current_user"]),
        role=NodeRole.STANDBY if metadata["in_recovery"] else NodeRole.PRIMARY,
        transaction_read_only=bool(metadata["transaction_read_only"]),
        wal_level=str(metadata["wal_level"]),
        max_wal_senders=int(metadata["max_wal_senders"]),
        max_replication_slots=int(metadata["max_replication_slots"]),
        hot_standby=bool(metadata["hot_standby"]),
        synchronous_standby_names_configured=bool(metadata["synchronous_standby_names_configured"]),
        replay_paused=bool(metadata["replay_paused"]),
        current_wal_lsn=_optional_text(metadata.get("current_wal_lsn")),
        received_wal_lsn=_optional_text(metadata.get("received_wal_lsn")),
        replayed_wal_lsn=_optional_text(metadata.get("replayed_wal_lsn")),
        replay_lag_bytes=_optional_int(metadata.get("replay_lag_bytes")),
        standbys=standbys,
        slots=slots,
        wal_receiver=receiver,
        subscriptions=subscriptions,
        publications=publications,
        unavailable=unavailable,
    )


class PostgresReplicationRepository:
    """Capture a consistent read-only replication snapshot on one connection."""

    def __init__(self, sql_driver: SqlDriver, *, timeout_seconds: float = 30.0):
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        self._driver = sql_driver
        self._timeout_seconds = timeout_seconds

    @staticmethod
    async def _rollback(connection: Any) -> bool:
        try:
            await asyncio.shield(connection.rollback())
        except Exception:
            return False
        return True

    @staticmethod
    async def _rows(cursor: Any, sql: str, params: list[Any] | None = None) -> list[Mapping[str, Any]]:
        await cursor.execute(sql, params)
        return [_mapping(row) for row in await cursor.fetchall()]

    @staticmethod
    async def _one(cursor: Any, sql: str, params: list[Any] | None = None) -> Mapping[str, Any]:
        await cursor.execute(sql, params)
        row = _mapping(await cursor.fetchone())
        if not row:
            raise ReplicationExecutionError("PostgreSQL returned no replication metadata")
        return row

    async def _optional_rows(
        self,
        cursor: Any,
        sql: str,
        params: list[Any] | None,
        *,
        capability: str,
        unavailable: list[str],
    ) -> list[Mapping[str, Any]]:
        savepoint = "pgsql_mcp_replication_optional"
        await cursor.execute(f"SAVEPOINT {savepoint}")
        try:
            rows = await self._rows(cursor, sql, params)
        except _OPTIONAL_ERRORS:
            await cursor.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            await cursor.execute(f"RELEASE SAVEPOINT {savepoint}")
            unavailable.append(capability)
            return []
        await cursor.execute(f"RELEASE SAVEPOINT {savepoint}")
        return rows

    async def capture(self, *, limit: int) -> ReplicationTopology:
        rolled_back: bool | None = None
        try:
            async with asyncio.timeout(self._timeout_seconds):
                async with self._driver.connection() as connection:
                    started = False
                    try:
                        async with connection.cursor(row_factory=dict_row) as cursor:
                            started = True
                            await cursor.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
                            await cursor.execute(
                                "SELECT set_config('statement_timeout', %s, true)",
                                [f"{max(1, int(self._timeout_seconds * 1000))}ms"],
                            )
                            await cursor.execute("SELECT set_config('row_security', 'on', true)")
                            await cursor.execute("SELECT set_config('search_path', 'pg_catalog', true)")
                            await cursor.execute("SELECT set_config('application_name', 'pgsql-mcp:replication', true)")
                            metadata = await self._one(cursor, _METADATA_SQL)
                            unavailable: list[str] = []
                            standby_rows = await self._optional_rows(
                                cursor,
                                _STANDBYS_SQL,
                                [limit],
                                capability="physical_standbys",
                                unavailable=unavailable,
                            )
                            slot_rows = await self._optional_rows(
                                cursor,
                                _SLOTS_SQL,
                                [limit],
                                capability="replication_slots",
                                unavailable=unavailable,
                            )
                            receiver_rows = await self._optional_rows(
                                cursor,
                                _RECEIVER_SQL,
                                None,
                                capability="wal_receiver",
                                unavailable=unavailable,
                            )
                            subscription_rows = await self._optional_rows(
                                cursor,
                                _SUBSCRIPTIONS_SQL,
                                [limit],
                                capability="logical_subscriptions",
                                unavailable=unavailable,
                            )
                            publication_rows = await self._optional_rows(
                                cursor,
                                _PUBLICATIONS_SQL,
                                [limit],
                                capability="logical_publications",
                                unavailable=unavailable,
                            )
                            result = topology_from_rows(
                                metadata,
                                standby_rows=standby_rows,
                                slot_rows=slot_rows,
                                receiver_row=receiver_rows[0] if receiver_rows else None,
                                subscription_rows=subscription_rows,
                                publication_rows=publication_rows,
                                unavailable=tuple(unavailable),
                            )
                        rolled_back = await self._rollback(connection)
                        started = False
                        if not rolled_back:
                            raise ReplicationExecutionError("replication snapshot completed but transaction cleanup could not be confirmed")
                        return result
                    except asyncio.CancelledError:
                        if started:
                            await self._rollback(connection)
                        raise
                    except ReplicationExecutionError:
                        if started:
                            rolled_back = await self._rollback(connection)
                            if not rolled_back:
                                raise ReplicationExecutionError("replication snapshot failed and rollback could not be confirmed") from None
                        raise
                    except BaseException as exc:
                        if started:
                            rolled_back = await self._rollback(connection)
                        if not isinstance(exc, Exception):
                            raise
                        raise ReplicationExecutionError("PostgreSQL replication snapshot failed") from exc
        except TimeoutError as exc:
            raise ReplicationExecutionError("replication snapshot timed out") from exc
