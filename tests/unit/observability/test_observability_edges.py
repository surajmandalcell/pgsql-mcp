"""Edge contracts required by the observability changed-line gate."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import http.client
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from mcp.types import TextContent

import postgres_mcp.observability as observability
import postgres_mcp.observed_server as observed_server


def test_registry_rejects_invalid_cardinality_and_reuses_existing_tool() -> None:
    with pytest.raises(ValueError, match="positive"):
        observability.MetricsRegistry(max_tools=0)

    ticks = iter((10, 20, 5, 30))
    registry = observability.MetricsRegistry(clock_ns=lambda: next(ticks))
    first = registry.start("same")
    second = registry.start("same")
    registry.finish(first, observability.ToolOutcome.SUCCESS)
    registry.finish(second, observability.ToolOutcome.SUCCESS)

    snapshot = registry.snapshot()
    assert len(snapshot["tools"]) == 1
    assert snapshot["tools"][0]["duration_sum_seconds"] == pytest.approx(1e-8)


def test_registry_recovers_when_context_token_cannot_be_reset(monkeypatch: pytest.MonkeyPatch) -> None:
    class FailingContext:
        def __init__(self) -> None:
            self.values: list[str | None] = []

        def set(self, value: str | None) -> object:
            self.values.append(value)
            return object()

        def reset(self, _token: object) -> None:
            raise ValueError("different context")

        def get(self) -> str | None:
            return self.values[-1] if self.values else None

    context = FailingContext()
    monkeypatch.setattr(observability, "_current_correlation_id", context)
    ticks = iter((0, 1))
    registry = observability.MetricsRegistry(clock_ns=lambda: next(ticks))

    token = registry.start("tool")
    registry.finish(token, observability.ToolOutcome.SUCCESS)

    assert context.values == [token.correlation_id, None]


@pytest.mark.parametrize(
    ("contents", "outcome"),
    [
        ([SimpleNamespace()], observability.ToolOutcome.SUCCESS),
        ([TextContent(type="text", text="ordinary text")], observability.ToolOutcome.SUCCESS),
        ([TextContent(type="text", text="x" * 65_537)], observability.ToolOutcome.SUCCESS),
        ([TextContent(type="text", text="Error: ordinary failure")], observability.ToolOutcome.ERROR),
        ([TextContent(type="text", text="Error: EXPLAIN ANALYZE is disabled in restricted mode")], observability.ToolOutcome.DENIED),
        ([TextContent(type="text", text="[]")], observability.ToolOutcome.SUCCESS),
        ([TextContent(type="text", text='{"status":"denied"}')], observability.ToolOutcome.DENIED),
        ([TextContent(type="text", text='{"status":"failed"}')], observability.ToolOutcome.ERROR),
        ([TextContent(type="text", text='{"error":"redacted"}')], observability.ToolOutcome.ERROR),
        ([TextContent(type="text", text="{broken")], observability.ToolOutcome.SUCCESS),
    ],
)
def test_result_classifier_covers_every_nonsecret_control_shape(
    contents: list[object],
    outcome: observability.ToolOutcome,
) -> None:
    assert observability.classify_tool_result(contents).outcome is outcome


def test_label_escaping_and_loopback_detection_are_explicit() -> None:
    assert observability._escape_label('a\\b\n"c') == 'a\\\\b\\n\\"c'
    assert observability._is_loopback_host("localhost") is True
    assert observability._is_loopback_host("::1") is True
    assert observability._is_loopback_host("not-an-ip") is False


def test_metrics_http_server_validates_port_and_lifecycle_edges() -> None:
    registry = observability.MetricsRegistry()
    with pytest.raises(ValueError, match="between"):
        observability.MetricsHttpServer(registry, port=-1)
    with pytest.raises(ValueError, match="between"):
        observability.MetricsHttpServer(registry, port=65_536)

    server = observability.MetricsHttpServer(registry, host="localhost", port=0)
    with pytest.raises(RuntimeError, match="has not started"):
        _ = server.address

    address = server.start()
    assert server.start() == address
    host, port = address
    try:
        connection = http.client.HTTPConnection(host, port, timeout=2)
        connection.request("HEAD", "/metrics")
        head = connection.getresponse()
        assert head.status == 200
        assert head.read() == b""

        connection.request("GET", "/missing")
        missing = connection.getresponse()
        assert missing.status == 404
        assert missing.read() == b"not found\n"
        connection.close()
    finally:
        server.stop()


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, False), ("1", True), ("YES", True), ("off", False), ("0", False)],
)
def test_environment_flag_covers_default_true_and_false(
    monkeypatch: pytest.MonkeyPatch,
    value: str | None,
    expected: bool,
) -> None:
    if value is None:
        monkeypatch.delenv("FLAG", raising=False)
    else:
        monkeypatch.setenv("FLAG", value)
    assert observed_server._environment_flag("FLAG") is expected


@pytest.mark.asyncio
async def test_observed_main_runs_without_metrics_exporter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(observed_server, "metrics_http_server_from_environment", lambda: None)
    full_main = AsyncMock()
    monkeypatch.setattr(observed_server.full_server, "main", full_main)

    await observed_server.main()

    full_main.assert_awaited_once_with()
