"""Boundary contracts for the observable full-server entry point."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock
from unittest.mock import Mock

import pytest
from mcp.types import TextContent

import postgres_mcp
import postgres_mcp.observed_server as observed
from postgres_mcp.observability import MetricsRegistry
from postgres_mcp.observability import ToolOutcome


def response_payload(response: observed.full_server.ResponseType) -> object:
    content = response[0]
    assert isinstance(content, TextContent)
    return json.loads(content.text)


def test_full_console_entry_point_remains_lazy_and_selects_observed_server(monkeypatch: pytest.MonkeyPatch) -> None:
    run = Mock()
    monkeypatch.setattr(postgres_mcp, "_run", run)

    postgres_mcp.main()

    run.assert_called_once_with(".observed_server")


@pytest.mark.asyncio
async def test_runtime_metrics_tool_returns_aggregate_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    times = iter((0, 10_000_000))
    registry = MetricsRegistry(clock_ns=lambda: next(times))
    token = registry.start("execute_sql")
    registry.finish(token, ToolOutcome.SUCCESS)
    monkeypatch.setattr(observed, "runtime_metrics", registry)

    payload = response_payload(await observed.get_runtime_metrics())

    assert isinstance(payload, dict)
    assert payload["schema_version"] == 1
    assert payload["tools"][0]["tool"] == "execute_sql"
    assert "arguments" not in payload


def test_metrics_environment_is_disabled_by_default_and_validated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("METRICS_PORT", raising=False)
    assert observed.metrics_http_server_from_environment() is None

    monkeypatch.setenv("METRICS_PORT", "invalid")
    with pytest.raises(ValueError, match="integer"):
        observed.metrics_http_server_from_environment()

    monkeypatch.setenv("METRICS_PORT", "9000")
    monkeypatch.setenv("METRICS_HOST", "0.0.0.0")
    monkeypatch.delenv("ALLOW_REMOTE_METRICS", raising=False)
    with pytest.raises(ValueError, match="explicit opt-in"):
        observed.metrics_http_server_from_environment()

    monkeypatch.setenv("ALLOW_REMOTE_METRICS", "true")
    server = observed.metrics_http_server_from_environment()
    assert server is not None
    assert server.host == "0.0.0.0"
    assert server.port == 9000

    monkeypatch.setenv("ALLOW_REMOTE_METRICS", "sometimes")
    with pytest.raises(ValueError, match="boolean"):
        observed.metrics_http_server_from_environment()


@pytest.mark.asyncio
async def test_main_stops_metrics_exporter_when_full_server_exits(monkeypatch: pytest.MonkeyPatch) -> None:
    metrics_server = Mock()
    metrics_server.start.return_value = ("127.0.0.1", 9191)
    monkeypatch.setattr(observed, "metrics_http_server_from_environment", lambda: metrics_server)
    full_main = AsyncMock()
    monkeypatch.setattr(observed.full_server, "main", full_main)

    await observed.main()

    metrics_server.start.assert_called_once_with()
    full_main.assert_awaited_once_with()
    metrics_server.stop.assert_called_once_with()
