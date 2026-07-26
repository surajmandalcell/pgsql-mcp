"""Supported-PostgreSQL contracts for replication topology diagnostics."""

from __future__ import annotations

import uuid

import pytest

from postgres_mcp.replication import NodeRole
from postgres_mcp.replication import PostgresReplicationRepository
from postgres_mcp.replication import ReplicationService
from postgres_mcp.sql import DbConnPool
from postgres_mcp.sql import SqlDriver


@pytest.mark.asyncio
async def test_primary_topology_and_physical_slot_are_discovered(
    test_postgres_connection_string: tuple[str, str],
) -> None:
    connection_string, _image = test_postgres_connection_string
    driver = SqlDriver(engine_url=connection_string)
    service = ReplicationService(PostgresReplicationRepository(driver, timeout_seconds=30))
    slot_name = f"mcp_ha_{uuid.uuid4().hex[:12]}"

    try:
        await driver.execute_query(
            "SELECT pg_catalog.pg_create_physical_replication_slot(%s, true)",
            params=[slot_name],
            force_readonly=False,
        )
        topology = await service.topology(limit=20)
        assessment = await service.assess(limit=20)

        assert topology.role is NodeRole.PRIMARY
        assert topology.server_version_num // 10_000 in {14, 15, 16, 17, 18}
        assert topology.database == "test_db"
        assert topology.wal_level in {"replica", "logical"}
        assert any(slot.slot_name == slot_name and slot.slot_type == "physical" for slot in topology.slots)
        assert topology.wal_receiver is None
        assert "conninfo" not in str(topology.to_payload()).lower()
        assert assessment.topology_summary["role"] == "primary"
    finally:
        try:
            await driver.execute_query(
                "SELECT pg_catalog.pg_drop_replication_slot(%s)",
                params=[slot_name],
                force_readonly=False,
            )
        finally:
            if isinstance(driver.conn, DbConnPool):
                await driver.conn.close()
