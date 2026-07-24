"""Transport configuration shared by pgsql-mcp server profiles."""

from __future__ import annotations

import logging
import os
from argparse import Namespace
from typing import Any

DEFAULT_SSE_HOST = "localhost"
DEFAULT_SSE_PORT = 8000
DEFAULT_SSE_PATH = "/sse"

logger = logging.getLogger(__name__)


def env_number(name: str, default: int | float, converter: type[int] | type[float]) -> int | float:
    """Read a numeric environment value with a deterministic fallback."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return converter(raw)
    except ValueError:
        logger.warning("Invalid %s value %r; using %s", name, raw, default)
        return default


async def run_transport(mcp: Any, args: Namespace) -> None:
    """Run stdio or loopback-by-default SSE transport."""
    if args.transport == "stdio":
        await mcp.run_stdio_async()
        return

    host = args.sse_host or os.environ.get("SSE_HOST", DEFAULT_SSE_HOST)
    port = args.sse_port
    if port is None:
        port = int(env_number("SSE_PORT", DEFAULT_SSE_PORT, int))
    path = args.sse_path or os.environ.get("SSE_PATH", DEFAULT_SSE_PATH)
    cors_value = args.cors_allow_origins or os.environ.get("CORS_ALLOW_ORIGINS")

    if cors_value:
        import uvicorn
        from starlette.applications import Starlette
        from starlette.middleware import Middleware
        from starlette.middleware.cors import CORSMiddleware
        from starlette.routing import Mount

        origins = ["*"] if cors_value == "*" else [item.strip() for item in cors_value.split(",") if item.strip()]
        middleware = [
            Middleware(
                CORSMiddleware,
                allow_origins=origins,
                allow_credentials=origins != ["*"],
                allow_methods=["GET", "POST", "OPTIONS"],
                allow_headers=["*"],
            )
        ]
        mcp.settings.sse_path = path
        app = Starlette(routes=[Mount("/", app=mcp.sse_app())], middleware=middleware)
        await uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="info")).serve()
        return

    mcp.settings.host = host
    mcp.settings.port = port
    mcp.settings.sse_path = path
    await mcp.run_sse_async()
