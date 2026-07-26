# ruff: noqa: B008
"""Focused read-only PostgreSQL replication and failover-readiness MCP server."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
from typing import Annotated
from typing import Any

import mcp.types as types
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from pydantic import Field

from .replication import MAX_REPLICATION_ROWS
from .replication import PostgresReplicationRepository
from .replication import ReplicationError
from .replication import ReplicationService
from .replication import ReplicationThresholds
from .runtime import DEFAULT_QUERY_TIMEOUT_SECONDS
from .sql import DbConnPool
from .sql import SqlDriver
from .sql import json_text
from .sql import obfuscate_password
from .transport import env_number
from .transport import run_transport

load_dotenv()

logger = logging.getLogger(__name__)
mcp = FastMCP("postgres-mcp-ha")
ResponseType = list[types.TextContent | types.ImageContent | types.EmbeddedResource]

# HA diagnostics are intentionally read-only and bounded. Two connections are
# enough for editor and incident-response workflows without creating pool load.
db_connection = DbConnPool(min_size=0, max_size=2)
current_query_timeout = DEFAULT_QUERY_TIMEOUT_SECONDS
shutdown_in_progress = False


def format_text_response(value: Any) -> ResponseType:
    """Render a compact, loss-aware MCP text response."""
    rendered = value if isinstance(value, str) else json_text(value)
    return [types.TextContent(type="text", text=rendered)]


def format_error_response(error: str) -> ResponseType:
    """Render a stable error response without transport or credential details."""
    return format_text_response(f"Error: {error}")


def get_replication_service() -> ReplicationService:
    """Build the replication use-case service around the lazy shared pool."""
    return ReplicationService(
        PostgresReplicationRepository(
            SqlDriver(conn=db_connection),
            timeout_seconds=current_query_timeout,
        )
    )


@mcp.tool(description="Report the read-only PostgreSQL HA profile and its bounded tool surface")
async def get_server_capabilities() -> ResponseType:
    """Describe the deterministic HA-diagnostics profile."""
    return format_text_response(
        {
            "server": "pgsql-mcp",
            "profile": "ha",
            "safe_by_default": True,
            "read_only": True,
            "pool": {"min_size": 0, "max_size": 2},
            "timeout_seconds": current_query_timeout,
            "max_rows_per_catalog": MAX_REPLICATION_ROWS,
            "tools": [
                "get_server_capabilities",
                "get_replication_topology",
                "assess_failover_readiness",
            ],
            "secret_redaction": {
                "subscription_connection_strings": "never selected",
                "wal_receiver_conninfo": "never selected",
                "database_url": "never returned",
            },
            "omitted": [
                "writes",
                "raw_sql",
                "migrations",
                "maintenance",
                "index_advisor",
                "llm_features",
            ],
        }
    )


@mcp.tool(description="Capture bounded physical and logical PostgreSQL replication topology without connection secrets")
async def get_replication_topology(
    limit: Annotated[int, Field(description="Maximum rows per replication catalog", ge=1, le=MAX_REPLICATION_ROWS)] = 50,
) -> ResponseType:
    """Return one consistent read-only topology snapshot."""
    try:
        topology = await get_replication_service().topology(limit=limit)
        return format_text_response(topology.to_payload())
    except (ReplicationError, ValueError) as exc:
        return format_error_response(str(exc))
    except Exception as exc:
        logger.exception("Unexpected replication topology error")
        return format_error_response(str(exc))


@mcp.tool(description="Assess deterministic PostgreSQL failover readiness from a fresh bounded topology snapshot")
async def assess_failover_readiness(
    limit: Annotated[int, Field(description="Maximum rows per replication catalog", ge=1, le=MAX_REPLICATION_ROWS)] = 50,
    warning_lag_bytes: Annotated[int, Field(description="Warning threshold for WAL byte lag", ge=0)] = 64 * 1024 * 1024,
    critical_lag_bytes: Annotated[int, Field(description="Critical threshold for WAL byte lag", ge=0)] = 1024 * 1024 * 1024,
    warning_lag_seconds: Annotated[float, Field(description="Warning threshold for replay lag seconds", ge=0)] = 30.0,
    critical_lag_seconds: Annotated[float, Field(description="Critical threshold for replay lag seconds", ge=0)] = 300.0,
    warning_inactive_slot_bytes: Annotated[int, Field(description="Warning retained-WAL threshold for inactive slots", ge=0)] = 1024 * 1024 * 1024,
    critical_inactive_slot_bytes: Annotated[
        int,
        Field(description="Critical retained-WAL threshold for inactive slots", ge=0),
    ] = 10 * 1024 * 1024 * 1024,
) -> ResponseType:
    """Capture topology and return answer-first readiness findings."""
    try:
        thresholds = ReplicationThresholds(
            warning_lag_bytes=warning_lag_bytes,
            critical_lag_bytes=critical_lag_bytes,
            warning_lag_seconds=warning_lag_seconds,
            critical_lag_seconds=critical_lag_seconds,
            warning_inactive_slot_bytes=warning_inactive_slot_bytes,
            critical_inactive_slot_bytes=critical_inactive_slot_bytes,
        )
        result = await get_replication_service().assess(thresholds=thresholds, limit=limit)
        return format_text_response(result.to_payload())
    except (ReplicationError, ValueError) as exc:
        return format_error_response(str(exc))
    except Exception as exc:
        logger.exception("Unexpected failover readiness error")
        return format_error_response(str(exc))


def build_argument_parser() -> argparse.ArgumentParser:
    """Build the deliberately narrow HA-diagnostics command-line interface."""
    parser = argparse.ArgumentParser(description="PostgreSQL MCP HA Diagnostics Server")
    parser.add_argument("database_url", help="Database connection URL", nargs="?")
    parser.add_argument("--transport", choices=["stdio", "sse"], default="stdio")
    parser.add_argument("--sse-host", default=None)
    parser.add_argument("--sse-port", type=int, default=None)
    parser.add_argument("--sse-path", default=None)
    parser.add_argument("--cors-allow-origins", default=None)
    parser.add_argument("--query-timeout", type=float, default=None)
    return parser


async def main() -> None:
    """Configure and run the focused HA diagnostics server."""
    args = build_argument_parser().parse_args()

    global current_query_timeout
    current_query_timeout = (
        args.query_timeout if args.query_timeout is not None else float(env_number("QUERY_TIMEOUT", DEFAULT_QUERY_TIMEOUT_SECONDS, float))
    )
    if current_query_timeout <= 0:
        raise ValueError("query timeout must be greater than zero")

    database_url = os.environ.get("DATABASE_URI", args.database_url)
    if not database_url:
        raise ValueError("No database URL provided. Set DATABASE_URI or pass the positional database_url.")

    try:
        await db_connection.pool_connect(database_url)
        logger.info("Connected to PostgreSQL in HA diagnostics profile")
    except Exception as exc:
        logger.warning("Could not connect to database: %s", obfuscate_password(str(exc)))
        logger.warning("The server will start, but replication tools will fail until the connection is valid.")

    try:
        loop = asyncio.get_running_loop()
        for current_signal in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(
                current_signal,
                lambda selected=current_signal: asyncio.create_task(shutdown(selected)),
            )
    except NotImplementedError:
        logger.warning("Signal handling is not supported on this platform")

    await run_transport(mcp, args)


async def shutdown(sig: signal.Signals | None = None) -> None:
    """Close HA resources and exit with a signal-compatible code."""
    global shutdown_in_progress
    if shutdown_in_progress:
        raise SystemExit(1)
    shutdown_in_progress = True
    await db_connection.close()
    raise SystemExit(128 + int(sig) if sig is not None else 0)
