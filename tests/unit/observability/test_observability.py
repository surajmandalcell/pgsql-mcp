"""Contracts for privacy-preserving runtime observability."""

from __future__ import annotations

import asyncio
import http.client
from types import SimpleNamespace
from typing import Any

import pytest
from mcp.types import TextContent

from postgres_mcp.observability import MetricsHttpServer
from postgres_mcp.observability import MetricsRegistry
from postgres_mcp.observability import ToolOutcome
from postgres_mcp.observability import classify_tool_result
from postgres_mcp.observability import current_correlation_id
from postgres_mcp.observability import install_fastmcp_observability


def test_registry_records_bounded_aggregate_signals() -> None:
    times = iter((1_000_000_000, 1_250_000_000))
    registry = MetricsRegistry(
        max_tools=2,
        clock_ns=lambda: next(times),
        correlation_id_factory=lambda: "correlation-1",
    )

    token = registry.start("execute_sql")
    assert current_correlation_id() == "correlation-1"
    registry.finish(token, ToolOutcome.SUCCESS, truncated=True, rolled_back=True)
    registry.finish(token, ToolOutcome.ERROR)

    snapshot = registry.snapshot()
    assert snapshot["active"] == 0
    assert snapshot["tools"] == [
        {
            "tool": "execute_sql",
            "active": 0,
            "outcomes": {
                "success": 1,
                "error": 0,
                "denied": 0,
                "cancelled": 0,
                "unknown": 0,
            },
            "duration_count": 1,
            "duration_sum_seconds": 0.25,
            "duration_buckets": {
                "0.005": 0,
                "0.01": 0,
                "0.025": 0,
                "0.05": 0,
                "0.1": 0,
                "0.25": 1,
                "0.5": 1,
                "1": 1,
                "2.5": 1,
                "5": 1,
                "10": 1,
            },
            "truncated": 1,
            "rolled_back": 1,
        }
    ]
    assert current_correlation_id() is None


def test_registry_caps_tool_label_cardinality_and_sanitizes_names() -> None:
    clock = iter((0, 1, 2, 3, 4, 5))
    registry = MetricsRegistry(max_tools=1, clock_ns=lambda: next(clock))
    first = registry.start("first")
    registry.finish(first, ToolOutcome.SUCCESS)
    second = registry.start("second")
    registry.finish(second, ToolOutcome.ERROR)
    invalid = registry.start("contains secret whitespace")
    registry.finish(invalid, ToolOutcome.DENIED)

    names = [item["tool"] for item in registry.snapshot()["tools"]]
    assert names == ["__other__", "first"]


def test_result_classifier_uses_control_fields_without_retaining_values() -> None:
    denied = classify_tool_result([TextContent(type="text", text="Error: insert_rows requires unrestricted mode")])
    assert denied.outcome is ToolOutcome.DENIED

    unknown = classify_tool_result(
        [
            TextContent(
                type="text",
                text='{"outcome":"unknown","truncated":true,"rolled_back":true,"private":"do-not-store"}',
            )
        ]
    )
    assert unknown.outcome is ToolOutcome.UNKNOWN
    assert unknown.truncated is True
    assert unknown.rolled_back is True

    malformed = classify_tool_result([TextContent(type="text", text="{not-json")])
    assert malformed.outcome is ToolOutcome.SUCCESS


@pytest.mark.asyncio
async def test_fastmcp_installer_records_success_error_and_cancellation_without_arguments() -> None:
    registry = MetricsRegistry(correlation_id_factory=lambda: "call-id")
    observed_ids: list[str | None] = []

    async def success(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        observed_ids.append(current_correlation_id())
        assert name == "select_rows"
        assert arguments == {"password": "never-record-this"}
        return [TextContent(type="text", text='{"truncated":true}')]

    server = SimpleNamespace(call_tool=success)
    install_fastmcp_observability(server, registry)
    install_fastmcp_observability(server, registry)
    await server.call_tool("select_rows", {"password": "never-record-this"})

    async def failure(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        raise RuntimeError("database secret")

    failing_server = SimpleNamespace(call_tool=failure)
    install_fastmcp_observability(failing_server, registry)
    with pytest.raises(RuntimeError, match="database secret"):
        await failing_server.call_tool("execute_sql", {"sql": "secret"})

    async def cancelled(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        raise asyncio.CancelledError

    cancelled_server = SimpleNamespace(call_tool=cancelled)
    install_fastmcp_observability(cancelled_server, registry)
    with pytest.raises(asyncio.CancelledError):
        await cancelled_server.call_tool("get_server_info", {})

    assert observed_ids == ["call-id"]
    rendered = registry.prometheus_text()
    assert "never-record-this" not in rendered
    assert "database secret" not in rendered
    assert 'tool="select_rows",outcome="success"} 1' in rendered
    assert 'tool="execute_sql",outcome="error"} 1' in rendered
    assert 'tool="get_server_info",outcome="cancelled"} 1' in rendered


def test_prometheus_export_is_deterministic_and_escapes_labels() -> None:
    times = iter((0, 5_000_000))
    registry = MetricsRegistry(clock_ns=lambda: next(times))
    token = registry.start("tool:name")
    registry.finish(token, ToolOutcome.SUCCESS)

    rendered = registry.prometheus_text()
    assert rendered.endswith("\n")
    assert rendered.count("pgsql_mcp_tool_duration_seconds_count") == 2
    assert 'pgsql_mcp_tool_active{tool="tool:name"} 0' in rendered
    assert 'le="+Inf"} 1' in rendered


def test_metrics_http_server_is_loopback_first_and_read_only() -> None:
    registry = MetricsRegistry()
    server = MetricsHttpServer(registry, port=0)
    host, port = server.start()
    try:
        connection = http.client.HTTPConnection(host, port, timeout=2)
        connection.request("GET", "/healthz")
        response = connection.getresponse()
        assert response.status == 200
        assert response.read() == b'{"status":"ok"}\n'
        assert response.getheader("Cache-Control") == "no-store"

        connection.request("GET", "/metrics?ignored=true")
        metrics_response = connection.getresponse()
        assert metrics_response.status == 200
        assert b"pgsql_mcp_tool_calls_total" in metrics_response.read()

        connection.request("POST", "/metrics", body=b"ignored")
        post_response = connection.getresponse()
        assert post_response.status == 405
        assert post_response.getheader("Allow") == "GET, HEAD"
        post_response.read()
        connection.close()
    finally:
        server.stop()
        server.stop()


def test_remote_metrics_binding_requires_explicit_opt_in() -> None:
    with pytest.raises(ValueError, match="explicit opt-in"):
        MetricsHttpServer(MetricsRegistry(), host="0.0.0.0", port=9000)

    allowed = MetricsHttpServer(MetricsRegistry(), host="0.0.0.0", port=0, allow_remote=True)
    assert allowed.host == "0.0.0.0"
