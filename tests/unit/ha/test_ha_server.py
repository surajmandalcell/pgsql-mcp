"""Contracts for the focused, read-only PostgreSQL HA server profile."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
from datetime import datetime
from datetime import timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from unittest.mock import Mock
from unittest.mock import patch

import pytest
from mcp.types import TextContent

import postgres_mcp
import postgres_mcp.ha_server as ha
from postgres_mcp.replication import FailoverReadinessAssessment
from postgres_mcp.replication import FindingSeverity
from postgres_mcp.replication import NodeRole
from postgres_mcp.replication import ReplicationExecutionError
from postgres_mcp.replication import ReplicationFinding
from postgres_mcp.replication import ReplicationThresholds
from postgres_mcp.replication import ReplicationTopology


def response_text(response: ha.ResponseType) -> str:
    content = response[0]
    assert isinstance(content, TextContent)
    return content.text


def response_payload(response: ha.ResponseType) -> object:
    return json.loads(response_text(response))


def topology() -> ReplicationTopology:
    return ReplicationTopology(
        captured_at=datetime(2026, 7, 26, tzinfo=timezone.utc),
        server_version_num=180001,
        database="app",
        current_user="monitor",
        role=NodeRole.PRIMARY,
        transaction_read_only=False,
        wal_level="replica",
        max_wal_senders=10,
        max_replication_slots=10,
        hot_standby=True,
        synchronous_standby_names_configured=False,
        replay_paused=False,
        current_wal_lsn="0/100",
        received_wal_lsn=None,
        replayed_wal_lsn=None,
        replay_lag_bytes=None,
    )


def assessment() -> FailoverReadinessAssessment:
    return FailoverReadinessAssessment("ready", True, (), {"role": "primary"})


def args(**overrides):
    values = {
        "database_url": "postgresql://postgres@localhost/app",
        "transport": "stdio",
        "sse_host": None,
        "sse_port": None,
        "sse_path": None,
        "cors_allow_origins": None,
        "query_timeout": 15.0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_package_import_does_not_eagerly_load_ha_server() -> None:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(filter(None, ["src", environment.get("PYTHONPATH")]))
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import postgres_mcp; "
                "assert 'postgres_mcp.ha_server' not in sys.modules; "
                "assert 'postgres_mcp.replication' not in sys.modules"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert completed.returncode == 0, completed.stderr


def test_ha_entry_point_and_lazy_module_export() -> None:
    with patch.object(postgres_mcp, "_run") as runner:
        postgres_mcp.ha_main()
    runner.assert_called_once_with(".ha_server")

    assert postgres_mcp.__getattr__("ha_server") is ha
    with pytest.raises(AttributeError, match="missing"):
        postgres_mcp.__getattr__("missing")


@pytest.mark.asyncio
async def test_capabilities_are_read_only_secret_free_and_bounded() -> None:
    payload = response_payload(await ha.get_server_capabilities())

    assert isinstance(payload, dict)
    assert payload["profile"] == "ha"
    assert payload["read_only"] is True
    assert payload["pool"] == {"min_size": 0, "max_size": 2}
    assert payload["tools"] == [
        "get_server_capabilities",
        "get_replication_topology",
        "assess_failover_readiness",
    ]
    assert payload["secret_redaction"]["wal_receiver_conninfo"] == "never selected"
    assert "raw_sql" in payload["omitted"]


def test_format_helpers_and_service_factory() -> None:
    assert response_text(ha.format_text_response("ready")) == "ready"
    assert response_text(ha.format_error_response("failed")) == "Error: failed"

    service = ha.get_replication_service()
    assert service is not None
    assert ha.db_connection.min_size == 0
    assert ha.db_connection.max_size == 2


@pytest.mark.asyncio
async def test_topology_tool_returns_structured_payload() -> None:
    service = AsyncMock()
    service.topology.return_value = topology()

    with patch.object(ha, "get_replication_service", return_value=service):
        response = await ha.get_replication_topology(limit=25)

    payload = response_payload(response)
    assert isinstance(payload, dict)
    assert payload["role"] == "primary"
    service.topology.assert_awaited_once_with(limit=25)


@pytest.mark.asyncio
async def test_topology_tool_returns_stable_domain_and_unexpected_errors() -> None:
    domain_service = AsyncMock()
    domain_service.topology.side_effect = ReplicationExecutionError("denied")
    with patch.object(ha, "get_replication_service", return_value=domain_service):
        assert response_text(await ha.get_replication_topology()) == "Error: denied"

    unexpected_service = AsyncMock()
    unexpected_service.topology.side_effect = RuntimeError("broken")
    with patch.object(ha, "get_replication_service", return_value=unexpected_service):
        assert response_text(await ha.get_replication_topology()) == "Error: broken"


@pytest.mark.asyncio
async def test_assessment_tool_builds_exact_thresholds() -> None:
    service = AsyncMock()
    service.assess.return_value = assessment()

    with patch.object(ha, "get_replication_service", return_value=service):
        response = await ha.assess_failover_readiness(
            limit=12,
            warning_lag_bytes=1,
            critical_lag_bytes=2,
            warning_lag_seconds=3,
            critical_lag_seconds=4,
            warning_inactive_slot_bytes=5,
            critical_inactive_slot_bytes=6,
        )

    assert response_payload(response) == {
        "status": "ready",
        "ready": True,
        "topology_summary": {"role": "primary"},
        "findings": [],
    }
    service.assess.assert_awaited_once_with(
        thresholds=ReplicationThresholds(
            warning_lag_bytes=1,
            critical_lag_bytes=2,
            warning_lag_seconds=3,
            critical_lag_seconds=4,
            warning_inactive_slot_bytes=5,
            critical_inactive_slot_bytes=6,
        ),
        limit=12,
    )


@pytest.mark.asyncio
async def test_assessment_tool_returns_domain_and_unexpected_errors() -> None:
    response = await ha.assess_failover_readiness(warning_lag_bytes=2, critical_lag_bytes=1)
    assert "critical lag bytes" in response_text(response)

    service = AsyncMock()
    service.assess.side_effect = RuntimeError("broken")
    with patch.object(ha, "get_replication_service", return_value=service):
        assert response_text(await ha.assess_failover_readiness()) == "Error: broken"


@pytest.mark.asyncio
async def test_assessment_payload_can_contain_findings() -> None:
    finding = ReplicationFinding("lag", FindingSeverity.WARNING, "Lag detected.")
    service = AsyncMock()
    service.assess.return_value = FailoverReadinessAssessment("warning", False, (finding,), {"role": "primary"})
    with patch.object(ha, "get_replication_service", return_value=service):
        payload = response_payload(await ha.assess_failover_readiness())
    assert isinstance(payload, dict)
    assert payload["findings"][0]["code"] == "lag"


def test_cli_is_read_only_and_has_no_access_mode() -> None:
    parser = ha.build_argument_parser()
    parsed = parser.parse_args(["postgresql://localhost/app", "--query-timeout", "5"])
    assert parsed.query_timeout == 5
    with pytest.raises(SystemExit):
        parser.parse_args(["--access-mode=unrestricted"])


@pytest.mark.asyncio
async def test_main_requires_database_url_and_positive_timeout() -> None:
    parser = Mock()
    parser.parse_args.return_value = args(database_url=None)
    with (
        patch.object(ha, "build_argument_parser", return_value=parser),
        patch.dict(os.environ, {}, clear=True),
    ):
        with pytest.raises(ValueError, match="No database URL"):
            await ha.main()

    parser.parse_args.return_value = args(query_timeout=0)
    with patch.object(ha, "build_argument_parser", return_value=parser):
        with pytest.raises(ValueError, match="timeout"):
            await ha.main()


@pytest.mark.asyncio
async def test_main_connects_registers_signals_and_runs_transport() -> None:
    parser = Mock()
    parser.parse_args.return_value = args(query_timeout=None)
    loop = Mock()
    run = AsyncMock()
    connect = AsyncMock()
    previous_timeout = ha.current_query_timeout
    try:
        with (
            patch.object(ha, "build_argument_parser", return_value=parser),
            patch.object(ha.db_connection, "pool_connect", connect),
            patch.object(ha, "run_transport", run),
            patch.object(asyncio, "get_running_loop", return_value=loop),
            patch.object(ha, "env_number", return_value=11),
            patch.dict(os.environ, {"DATABASE_URI": "postgresql://env/app"}, clear=True),
        ):
            await ha.main()
    finally:
        ha.current_query_timeout = previous_timeout

    connect.assert_awaited_once_with("postgresql://env/app")
    assert loop.add_signal_handler.call_count == 2
    run.assert_awaited_once()


@pytest.mark.asyncio
async def test_main_continues_after_connection_failure_and_unsupported_signals() -> None:
    parser = Mock()
    parser.parse_args.return_value = args()
    loop = Mock()
    loop.add_signal_handler.side_effect = NotImplementedError
    run = AsyncMock()
    with (
        patch.object(ha, "build_argument_parser", return_value=parser),
        patch.object(ha.db_connection, "pool_connect", AsyncMock(side_effect=RuntimeError("postgresql://secret@host/app"))),
        patch.object(ha, "run_transport", run),
        patch.object(asyncio, "get_running_loop", return_value=loop),
        patch.dict(os.environ, {}, clear=True),
    ):
        await ha.main()
    run.assert_awaited_once()


@pytest.mark.asyncio
async def test_shutdown_closes_once_and_uses_signal_exit_code() -> None:
    previous = ha.shutdown_in_progress
    ha.shutdown_in_progress = False
    close = AsyncMock()
    try:
        with patch.object(ha.db_connection, "close", close):
            with pytest.raises(SystemExit) as error:
                await ha.shutdown(signal.SIGTERM)
            assert error.value.code == 128 + int(signal.SIGTERM)
            close.assert_awaited_once()
            with pytest.raises(SystemExit) as repeated:
                await ha.shutdown()
            assert repeated.value.code == 1
    finally:
        ha.shutdown_in_progress = previous
