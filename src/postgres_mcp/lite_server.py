# ruff: noqa: B008
"""Minimal PostgreSQL MCP server focused on reliable read-only workflows."""

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

from .artifacts import ErrorResult
from .artifacts import ExplainPlanArtifact
from .catalog import get_object_details_data
from .catalog import list_objects_data
from .catalog import list_schemas_data
from .explain import ExplainPlanTool
from .runtime import DEFAULT_MAX_ROWS
from .runtime import DEFAULT_QUERY_TIMEOUT_SECONDS
from .runtime import LITE_ABSOLUTE_MAX_ROWS
from .runtime import QueryLimits
from .runtime import ServerProfile
from .sql import DbConnPool
from .sql import SafeQueryExecutor
from .sql import SafeSqlDriver
from .sql import SqlDriver
from .sql import TransactionValidationError
from .sql import json_text
from .sql import obfuscate_password
from .transport import env_number
from .transport import run_transport

load_dotenv()

logger = logging.getLogger(__name__)
mcp = FastMCP("postgres-mcp-lite")

ResponseType = list[types.TextContent | types.ImageContent | types.EmbeddedResource]

# Lite keeps no warm connections and deliberately limits concurrency. It is
# intended for editor assistants and local automation, not operational fan-out.
db_connection = DbConnPool(min_size=0, max_size=2)
current_query_timeout = DEFAULT_QUERY_TIMEOUT_SECONDS
current_max_rows = min(DEFAULT_MAX_ROWS, LITE_ABSOLUTE_MAX_ROWS)
shutdown_in_progress = False


def format_text_response(value: Any) -> ResponseType:
    """Render one compact MCP text response."""
    rendered = value if isinstance(value, str) else json_text(value)
    return [types.TextContent(type="text", text=rendered)]


def format_error_response(error: str) -> ResponseType:
    """Render a stable error response without exposing transport internals."""
    return format_text_response(f"Error: {error}")


def get_base_sql_driver() -> SqlDriver:
    """Return the shared lazy-pool driver used by bounded operations."""
    return SqlDriver(conn=db_connection)


async def get_readonly_sql_driver() -> SafeSqlDriver:
    """Return a compatibility driver with AST and database read-only guards."""
    return SafeSqlDriver(
        sql_driver=get_base_sql_driver(),
        timeout=current_query_timeout,
    )


@mcp.tool(description="Report the lite profile, limits, and intentionally omitted capabilities")
async def get_server_capabilities() -> ResponseType:
    """Describe the deterministic, low-context lite tool surface."""
    return format_text_response(
        {
            "server": "pgsql-mcp",
            "profile": ServerProfile.LITE.value,
            "safe_by_default": True,
            "read_only": True,
            "transactions": False,
            "default_max_rows": current_max_rows,
            "absolute_max_rows": LITE_ABSOLUTE_MAX_ROWS,
            "timeout_seconds": current_query_timeout,
            "pool": {"min_size": 0, "max_size": 2},
            "tools": [
                "get_server_capabilities",
                "list_schemas",
                "list_objects",
                "get_object_details",
                "execute_sql",
                "explain_query",
            ],
            "omitted": [
                "writes",
                "migrations",
                "index_advisor",
                "workload_analysis",
                "database_health",
                "extension_management",
                "llm_features",
            ],
        }
    )


@mcp.tool(description="List PostgreSQL schemas")
async def list_schemas() -> ResponseType:
    """List schemas through trusted catalog SQL in a read-only transaction."""
    try:
        return format_text_response(await list_schemas_data(await get_readonly_sql_driver()))
    except Exception as exc:
        logger.exception("Error listing schemas")
        return format_error_response(str(exc))


@mcp.tool(description="List tables, views, sequences, or extensions")
async def list_objects(
    schema_name: Annotated[str, Field(description="Schema name")],
    object_type: Annotated[str, Field(description="table, view, sequence, or extension")] = "table",
) -> ResponseType:
    """List one bounded class of catalog objects."""
    try:
        return format_text_response(
            await list_objects_data(
                await get_readonly_sql_driver(),
                schema_name=schema_name,
                object_type=object_type,
            )
        )
    except Exception as exc:
        logger.exception("Error listing objects")
        return format_error_response(str(exc))


@mcp.tool(description="Show columns, constraints, indexes, and comments for one object")
async def get_object_details(
    schema_name: Annotated[str, Field(description="Schema name")],
    object_name: Annotated[str, Field(description="Object name")],
    object_type: Annotated[str, Field(description="table, view, sequence, or extension")] = "table",
) -> ResponseType:
    """Return focused metadata for one database object."""
    try:
        return format_text_response(
            await get_object_details_data(
                await get_readonly_sql_driver(),
                schema_name=schema_name,
                object_name=object_name,
                object_type=object_type,
            )
        )
    except Exception as exc:
        logger.exception("Error getting object details")
        return format_error_response(str(exc))


@mcp.tool(description="Execute one parameterized read-only statement with bounded results")
async def execute_sql(
    sql: Annotated[str, Field(description="Exactly one read-only PostgreSQL statement")],
    params: Annotated[list[Any] | None, Field(description="Native values for psycopg positional placeholders")] = None,
    max_rows: Annotated[
        int | None,
        Field(description="Maximum rows returned", ge=1, le=LITE_ABSOLUTE_MAX_ROWS),
    ] = None,
) -> ResponseType:
    """Execute a query using the same safety kernel as the full server."""
    try:
        limits = QueryLimits(
            timeout_seconds=current_query_timeout,
            default_max_rows=current_max_rows,
            absolute_max_rows=LITE_ABSOLUTE_MAX_ROWS,
        )
        limits.validate()
        row_limit = limits.checked_row_limit(max_rows)
        result = await SafeQueryExecutor(
            get_base_sql_driver(),
            timeout_seconds=current_query_timeout,
        ).execute_bounded_query(
            sql,  # type: ignore[arg-type]
            params=params,
            max_rows=row_limit,
        )
        return format_text_response(result.to_payload())
    except (TransactionValidationError, ValueError) as exc:
        return format_error_response(str(exc))
    except Exception as exc:
        logger.exception("Error executing SQL")
        return format_error_response(str(exc))


@mcp.tool(description="Generate a non-executing PostgreSQL EXPLAIN plan")
async def explain_query(
    sql: Annotated[str, Field(description="Read-only SQL query to explain")],
) -> ResponseType:
    """Explain a validated query without ANALYZE or hypothetical indexes."""
    try:
        executor = SafeQueryExecutor(
            get_base_sql_driver(),
            timeout_seconds=current_query_timeout,
        )
        await executor.validate_query(sql, parameter_count=0)
        result = await ExplainPlanTool(sql_driver=await get_readonly_sql_driver()).explain(sql)
        if isinstance(result, ExplainPlanArtifact):
            return format_text_response(result.to_text())
        if isinstance(result, ErrorResult):
            return format_error_response(result.to_text())
        return format_error_response("Error processing explain plan")
    except (TransactionValidationError, ValueError) as exc:
        return format_error_response(str(exc))
    except Exception as exc:
        logger.exception("Error explaining query")
        return format_error_response(str(exc))


def build_argument_parser() -> argparse.ArgumentParser:
    """Build the deliberately small lite command-line interface."""
    parser = argparse.ArgumentParser(description="PostgreSQL MCP Lite Server")
    parser.add_argument("database_url", help="Database connection URL", nargs="?")
    parser.add_argument("--transport", choices=["stdio", "sse"], default="stdio")
    parser.add_argument("--sse-host", default=None)
    parser.add_argument("--sse-port", type=int, default=None)
    parser.add_argument("--sse-path", default=None)
    parser.add_argument("--cors-allow-origins", default=None)
    parser.add_argument("--query-timeout", type=float, default=None)
    parser.add_argument("--max-rows", type=int, default=None)
    return parser


async def main() -> None:
    """Configure and run the lite server."""
    args = build_argument_parser().parse_args()

    global current_max_rows
    global current_query_timeout
    current_query_timeout = (
        args.query_timeout if args.query_timeout is not None else float(env_number("QUERY_TIMEOUT", DEFAULT_QUERY_TIMEOUT_SECONDS, float))
    )
    current_max_rows = args.max_rows if args.max_rows is not None else int(env_number("MAX_ROWS", min(DEFAULT_MAX_ROWS, LITE_ABSOLUTE_MAX_ROWS), int))
    QueryLimits(
        timeout_seconds=current_query_timeout,
        default_max_rows=current_max_rows,
        absolute_max_rows=LITE_ABSOLUTE_MAX_ROWS,
    ).validate()

    database_url = os.environ.get("DATABASE_URI", args.database_url)
    if not database_url:
        raise ValueError("No database URL provided. Set DATABASE_URI or pass the positional database_url.")

    try:
        await db_connection.pool_connect(database_url)
        logger.info("Connected to PostgreSQL in lite profile")
    except Exception as exc:
        logger.warning("Could not connect to database: %s", obfuscate_password(str(exc)))
        logger.warning("The server will start, but database tools will fail until the connection is valid.")

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
    """Close lite resources and exit with a signal-compatible code."""
    global shutdown_in_progress
    if shutdown_in_progress:
        raise SystemExit(1)
    shutdown_in_progress = True
    await db_connection.close()
    raise SystemExit(128 + int(sig) if sig is not None else 0)
