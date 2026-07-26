"""Observable full-server entry point with no effect on the lite profile."""

from __future__ import annotations

import logging
import os

from . import server as full_server
from .observability import MetricsHttpServer
from .observability import MetricsRegistry
from .observability import install_fastmcp_observability

logger = logging.getLogger(__name__)
runtime_metrics = MetricsRegistry()
install_fastmcp_observability(full_server.mcp, runtime_metrics)


async def get_runtime_metrics() -> full_server.ResponseType:
    """Return aggregate process metrics without request, SQL, or database values."""
    return full_server.format_text_response(runtime_metrics.snapshot())


full_server.mcp.add_tool(
    get_runtime_metrics,
    description="Return privacy-preserving aggregate runtime metrics for the current process",
)
mcp = full_server.mcp


def _environment_flag(name: str, *, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean value")


def metrics_http_server_from_environment() -> MetricsHttpServer | None:
    """Build the optional loopback-first exporter from environment configuration."""
    raw_port = os.environ.get("METRICS_PORT")
    if raw_port is None or not raw_port.strip():
        return None
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise ValueError("METRICS_PORT must be an integer") from exc
    return MetricsHttpServer(
        runtime_metrics,
        host=os.environ.get("METRICS_HOST", "127.0.0.1"),
        port=port,
        allow_remote=_environment_flag("ALLOW_REMOTE_METRICS"),
    )


async def main() -> None:
    """Run the normal full server with optional local metrics export."""
    metrics_server = metrics_http_server_from_environment()
    if metrics_server is not None:
        host, port = metrics_server.start()
        logger.info("Runtime metrics available on http://%s:%s", host, port)
    try:
        await full_server.main()
    finally:
        if metrics_server is not None:
            metrics_server.stop()
