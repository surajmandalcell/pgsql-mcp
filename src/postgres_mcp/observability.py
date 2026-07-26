"""Dependency-free, cardinality-bounded runtime observability.

The registry stores tool names and aggregate outcomes only. It never records MCP
arguments, SQL text, database identifiers, result values, or exception messages.
"""

from __future__ import annotations

import asyncio
import contextvars
import ipaddress
import json
import secrets
import threading
import time
from collections.abc import Callable
from collections.abc import Sequence
from dataclasses import dataclass
from dataclasses import field
from enum import Enum
from functools import wraps
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
from typing import Any
from typing import Protocol
from urllib.parse import urlsplit

_DURATION_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
_ALLOWED_TOOL_CHARACTERS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.:-")
_current_correlation_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "pgsql_mcp_correlation_id",
    default=None,
)


class ToolOutcome(str, Enum):
    """Stable aggregate outcomes that do not expose result details."""

    SUCCESS = "success"
    ERROR = "error"
    DENIED = "denied"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


@dataclass(slots=True)
class ObservationToken:
    """One in-flight observation owned by a single tool invocation."""

    tool: str
    started_ns: int
    correlation_id: str
    context_token: contextvars.Token[str | None]
    finished: bool = False


@dataclass(slots=True)
class _ToolMetrics:
    outcomes: dict[ToolOutcome, int] = field(default_factory=lambda: {outcome: 0 for outcome in ToolOutcome})
    active: int = 0
    duration_count: int = 0
    duration_sum: float = 0.0
    duration_buckets: list[int] = field(default_factory=lambda: [0] * len(_DURATION_BUCKETS))
    truncated: int = 0
    rolled_back: int = 0


@dataclass(frozen=True, slots=True)
class ClassifiedResult:
    """Privacy-preserving signals derived from a returned MCP envelope."""

    outcome: ToolOutcome
    truncated: bool = False
    rolled_back: bool = False


class ToolCallServer(Protocol):
    """Minimal FastMCP-compatible call surface used by the installer."""

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Sequence[Any]: ...


class MetricsRegistry:
    """Thread-safe metrics registry with a hard tool-label cardinality ceiling."""

    def __init__(
        self,
        *,
        max_tools: int = 128,
        clock_ns: Callable[[], int] = time.perf_counter_ns,
        correlation_id_factory: Callable[[], str] = lambda: secrets.token_hex(16),
    ) -> None:
        if max_tools < 1:
            raise ValueError("max_tools must be positive")
        self.max_tools = max_tools
        self._clock_ns = clock_ns
        self._correlation_id_factory = correlation_id_factory
        self._lock = threading.Lock()
        self._tools: dict[str, _ToolMetrics] = {}

    @staticmethod
    def _safe_tool_name(value: str) -> str:
        if not value or len(value) > 128 or any(character not in _ALLOWED_TOOL_CHARACTERS for character in value):
            return "__invalid__"
        return value

    def _select_tool_locked(self, requested: str) -> str:
        safe_name = self._safe_tool_name(requested)
        if safe_name in self._tools:
            return safe_name
        named_count = sum(name != "__other__" for name in self._tools)
        selected = safe_name if named_count < self.max_tools else "__other__"
        self._tools.setdefault(selected, _ToolMetrics())
        return selected

    def start(self, tool: str) -> ObservationToken:
        """Start one invocation without retaining any arguments or caller data."""
        with self._lock:
            selected = self._select_tool_locked(tool)
            self._tools[selected].active += 1
        correlation_id = self._correlation_id_factory()
        context_token = _current_correlation_id.set(correlation_id)
        return ObservationToken(selected, self._clock_ns(), correlation_id, context_token)

    def finish(
        self,
        token: ObservationToken,
        outcome: ToolOutcome,
        *,
        truncated: bool = False,
        rolled_back: bool = False,
    ) -> None:
        """Finish one observation exactly once and update cumulative aggregates."""
        if token.finished:
            return
        token.finished = True
        elapsed_seconds = max(0, self._clock_ns() - token.started_ns) / 1_000_000_000
        with self._lock:
            metrics = self._tools[token.tool]
            metrics.active = max(0, metrics.active - 1)
            metrics.outcomes[outcome] += 1
            metrics.duration_count += 1
            metrics.duration_sum += elapsed_seconds
            for index, boundary in enumerate(_DURATION_BUCKETS):
                if elapsed_seconds <= boundary:
                    metrics.duration_buckets[index] += 1
            metrics.truncated += int(truncated)
            metrics.rolled_back += int(rolled_back)
        try:
            _current_correlation_id.reset(token.context_token)
        except ValueError:
            _current_correlation_id.set(None)

    def snapshot(self) -> dict[str, Any]:
        """Return deterministic aggregate JSON without request or database data."""
        with self._lock:
            tools = []
            for name in sorted(self._tools):
                metrics = self._tools[name]
                tools.append(
                    {
                        "tool": name,
                        "active": metrics.active,
                        "outcomes": {outcome.value: metrics.outcomes[outcome] for outcome in ToolOutcome},
                        "duration_count": metrics.duration_count,
                        "duration_sum_seconds": metrics.duration_sum,
                        "duration_buckets": {
                            _format_boundary(boundary): metrics.duration_buckets[index] for index, boundary in enumerate(_DURATION_BUCKETS)
                        },
                        "truncated": metrics.truncated,
                        "rolled_back": metrics.rolled_back,
                    }
                )
            return {
                "schema_version": 1,
                "max_tools": self.max_tools,
                "active": sum(item["active"] for item in tools),
                "tools": tools,
            }

    def prometheus_text(self) -> str:
        """Render Prometheus exposition without adding a runtime dependency."""
        snapshot = self.snapshot()
        lines = [
            "# HELP pgsql_mcp_tool_calls_total Completed MCP tool calls by aggregate outcome.",
            "# TYPE pgsql_mcp_tool_calls_total counter",
        ]
        for item in snapshot["tools"]:
            tool = _escape_label(str(item["tool"]))
            outcomes = item["outcomes"]
            for outcome in ToolOutcome:
                lines.append(f'pgsql_mcp_tool_calls_total{{tool="{tool}",outcome="{outcome.value}"}} {outcomes[outcome.value]}')
        lines.extend(
            [
                "# HELP pgsql_mcp_tool_active Active MCP tool calls.",
                "# TYPE pgsql_mcp_tool_active gauge",
            ]
        )
        for item in snapshot["tools"]:
            tool = _escape_label(str(item["tool"]))
            lines.append(f'pgsql_mcp_tool_active{{tool="{tool}"}} {item["active"]}')
        lines.extend(
            [
                "# HELP pgsql_mcp_tool_duration_seconds MCP tool call duration.",
                "# TYPE pgsql_mcp_tool_duration_seconds histogram",
            ]
        )
        for item in snapshot["tools"]:
            tool = _escape_label(str(item["tool"]))
            buckets = item["duration_buckets"]
            for boundary in _DURATION_BUCKETS:
                label = _format_boundary(boundary)
                lines.append(f'pgsql_mcp_tool_duration_seconds_bucket{{tool="{tool}",le="{label}"}} {buckets[label]}')
            lines.append(f'pgsql_mcp_tool_duration_seconds_bucket{{tool="{tool}",le="+Inf"}} {item["duration_count"]}')
            lines.append(f'pgsql_mcp_tool_duration_seconds_sum{{tool="{tool}"}} {item["duration_sum_seconds"]:.9f}')
            lines.append(f'pgsql_mcp_tool_duration_seconds_count{{tool="{tool}"}} {item["duration_count"]}')
        lines.extend(
            [
                "# HELP pgsql_mcp_tool_truncated_total Tool responses that were explicitly truncated.",
                "# TYPE pgsql_mcp_tool_truncated_total counter",
                "# HELP pgsql_mcp_tool_rollback_total Tool responses that confirmed rollback.",
                "# TYPE pgsql_mcp_tool_rollback_total counter",
            ]
        )
        for item in snapshot["tools"]:
            tool = _escape_label(str(item["tool"]))
            lines.append(f'pgsql_mcp_tool_truncated_total{{tool="{tool}"}} {item["truncated"]}')
            lines.append(f'pgsql_mcp_tool_rollback_total{{tool="{tool}"}} {item["rolled_back"]}')
        return "\n".join(lines) + "\n"


def current_correlation_id() -> str | None:
    """Return the current call ID for structured logs or optional exporters."""
    return _current_correlation_id.get()


def classify_tool_result(result: Sequence[Any]) -> ClassifiedResult:
    """Extract aggregate control signals without retaining response contents."""
    outcome = ToolOutcome.SUCCESS
    truncated = False
    rolled_back = False
    for item in result:
        text = getattr(item, "text", None)
        if not isinstance(text, str):
            continue
        stripped = text.lstrip()
        if stripped.startswith("Error:"):
            normalized = stripped[:256].lower()
            if "requires" in normalized and "unrestricted" in normalized:
                outcome = ToolOutcome.DENIED
            elif "disabled in restricted" in normalized:
                outcome = ToolOutcome.DENIED
            else:
                outcome = ToolOutcome.ERROR
            break
        if not stripped.startswith("{") or len(stripped) > 65_536:
            continue
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        status = str(payload.get("outcome") or payload.get("status") or "").lower()
        if status == "unknown":
            outcome = ToolOutcome.UNKNOWN
        elif status in {"denied", "forbidden"}:
            outcome = ToolOutcome.DENIED
        elif status in {"error", "failed", "failure"} or bool(payload.get("error")):
            outcome = ToolOutcome.ERROR
        truncated = truncated or payload.get("truncated") is True
        rolled_back = rolled_back or payload.get("rolled_back") is True
    return ClassifiedResult(outcome, truncated, rolled_back)


def install_fastmcp_observability(server: ToolCallServer, registry: MetricsRegistry) -> ToolCallServer:
    """Instrument a FastMCP-compatible server once at its central call boundary."""
    server_object: Any = server
    if getattr(server_object, "_pgsql_mcp_observability_installed", False):
        return server
    original_call = server.call_tool

    @wraps(original_call)
    async def observed_call(name: str, arguments: dict[str, Any]) -> Sequence[Any]:
        token = registry.start(name)
        try:
            result = await original_call(name, arguments)
        except asyncio.CancelledError:
            registry.finish(token, ToolOutcome.CANCELLED)
            raise
        except Exception:
            registry.finish(token, ToolOutcome.ERROR)
            raise
        classified = classify_tool_result(result)
        registry.finish(
            token,
            classified.outcome,
            truncated=classified.truncated,
            rolled_back=classified.rolled_back,
        )
        return result

    server_object.call_tool = observed_call
    server_object._pgsql_mcp_observability_installed = True
    return server


class MetricsHttpServer:
    """Small loopback-first HTTP exporter for `/metrics` and `/healthz`."""

    def __init__(
        self,
        registry: MetricsRegistry,
        *,
        host: str = "127.0.0.1",
        port: int = 0,
        allow_remote: bool = False,
    ) -> None:
        if not 0 <= port <= 65_535:
            raise ValueError("metrics port must be between 0 and 65535")
        if not allow_remote and not _is_loopback_host(host):
            raise ValueError("remote metrics binding requires explicit opt-in")
        self.registry = registry
        self.host = host
        self.port = port
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def address(self) -> tuple[str, int]:
        if self._server is None:
            raise RuntimeError("metrics server has not started")
        host, port = self._server.server_address[:2]
        return str(host), int(port)

    def start(self) -> tuple[str, int]:
        if self._server is not None:
            return self.address
        registry = self.registry

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            server_version = "pgsql-mcp-metrics"
            sys_version = ""

            def _write(self, status: int, content_type: str, body: bytes, *, head_only: bool = False) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                if not head_only:
                    self.wfile.write(body)

            def _get(self, *, head_only: bool) -> None:
                path = urlsplit(self.path).path
                if path == "/metrics":
                    self._write(
                        200,
                        "text/plain; version=0.0.4; charset=utf-8",
                        registry.prometheus_text().encode("utf-8"),
                        head_only=head_only,
                    )
                    return
                if path == "/healthz":
                    body = b'{"status":"ok"}\n'
                    self._write(200, "application/json; charset=utf-8", body, head_only=head_only)
                    return
                self._write(404, "text/plain; charset=utf-8", b"not found\n", head_only=head_only)

            def do_GET(self) -> None:  # noqa: N802
                self._get(head_only=False)

            def do_HEAD(self) -> None:  # noqa: N802
                self._get(head_only=True)

            def do_POST(self) -> None:  # noqa: N802
                self.send_response(405)
                self.send_header("Allow", "GET, HEAD")
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, format: str, *args: Any) -> None:
                return

        self._server = ThreadingHTTPServer((self.host, self.port), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, name="pgsql-mcp-metrics", daemon=True)
        self._thread.start()
        return self.address

    def stop(self) -> None:
        server = self._server
        thread = self._thread
        self._server = None
        self._thread = None
        if server is None:
            return
        server.shutdown()
        server.server_close()
        if thread is not None:
            thread.join(timeout=5)


def _format_boundary(value: float) -> str:
    return f"{value:g}"


def _escape_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _is_loopback_host(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False
