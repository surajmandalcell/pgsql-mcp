# ruff: noqa: B008
"""Full-featured PostgreSQL MCP server."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
from typing import Annotated
from typing import Any
from typing import Literal

import mcp.types as types
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel
from pydantic import Field

load_dotenv()

from .artifacts import ErrorResult  # noqa: E402
from .artifacts import ExplainPlanArtifact  # noqa: E402
from .catalog import get_object_details_data  # noqa: E402
from .catalog import list_objects_data  # noqa: E402
from .catalog import list_schemas_data  # noqa: E402
from .explain import ExplainPlanTool  # noqa: E402
from .runtime import ABSOLUTE_MAX_ROWS  # noqa: E402
from .runtime import DEFAULT_LOCK_TIMEOUT_SECONDS  # noqa: E402
from .runtime import DEFAULT_MAX_ROWS  # noqa: E402
from .runtime import DEFAULT_QUERY_TIMEOUT_SECONDS  # noqa: E402
from .runtime import AccessMode  # noqa: E402
from .runtime import QueryLimits  # noqa: E402
from .runtime import ServerProfile  # noqa: E402
from .sql import DbConnPool  # noqa: E402
from .sql import IsolationLevel  # noqa: E402
from .sql import ResultMode  # noqa: E402
from .sql import SafeQueryExecutor  # noqa: E402
from .sql import SafeSqlDriver  # noqa: E402
from .sql import SqlDriver  # noqa: E402
from .sql import TransactionExecutionError  # noqa: E402
from .sql import TransactionStep  # noqa: E402
from .sql import TransactionValidationError  # noqa: E402
from .sql import check_hypopg_installation_status  # noqa: E402
from .sql import json_text  # noqa: E402
from .sql import obfuscate_password  # noqa: E402
from .transport import DEFAULT_SSE_HOST as DEFAULT_SSE_HOST  # noqa: E402
from .transport import DEFAULT_SSE_PATH as DEFAULT_SSE_PATH  # noqa: E402
from .transport import DEFAULT_SSE_PORT as DEFAULT_SSE_PORT  # noqa: E402
from .transport import env_number  # noqa: E402
from .transport import run_transport  # noqa: E402

mcp = FastMCP("postgres-mcp")

PG_STAT_STATEMENTS = "pg_stat_statements"
HYPOPG_EXTENSION = "hypopg"
DEFAULT_QUERY_TIMEOUT = int(DEFAULT_QUERY_TIMEOUT_SECONDS)
MAX_NUM_INDEX_TUNING_QUERIES = 10

ResponseType = list[types.TextContent | types.ImageContent | types.EmbeddedResource]
logger = logging.getLogger(__name__)


class HypotheticalIndex(BaseModel):
    """A hypothetical index definition used by EXPLAIN tooling."""

    table: str = Field(description="Table name, optionally schema-qualified")
    columns: list[str] = Field(description="Ordered columns in the hypothetical index")
    using: str = Field(default="btree", description="PostgreSQL index access method")


class TransactionStepInput(BaseModel):
    """One guarded statement in an atomic transaction request."""

    sql: str = Field(description="Exactly one SELECT, INSERT, UPDATE, DELETE, or MERGE statement")
    params: list[Any] = Field(default_factory=list, description="Native psycopg bind values for %s placeholders")
    expected_rows: int | None = Field(default=None, ge=0, description="Exact affected row count required for commit")
    max_affected_rows: int | None = Field(default=None, ge=1, description="Hard mutation ceiling required for writes")
    result_mode: Literal["none", "summary", "rows"] = Field(default="summary")
    max_rows: int = Field(default=DEFAULT_MAX_ROWS, ge=1, le=ABSOLUTE_MAX_ROWS)


# A process serves one configured database and one effective access policy.
db_connection = DbConnPool()
current_access_mode = AccessMode.RESTRICTED
current_profile = ServerProfile.FULL
current_query_timeout = DEFAULT_QUERY_TIMEOUT_SECONDS
current_max_rows = DEFAULT_MAX_ROWS
shutdown_in_progress = False


async def get_sql_driver() -> SqlDriver | SafeSqlDriver:
    """Return the compatibility driver used by internal analysis tools."""
    base_driver = SqlDriver(conn=db_connection)
    if current_access_mode is AccessMode.RESTRICTED:
        logger.debug("Using SafeSqlDriver with timeout=%ss", current_query_timeout)
        return SafeSqlDriver(sql_driver=base_driver, timeout=current_query_timeout)
    return base_driver


def get_base_sql_driver() -> SqlDriver:
    """Return the driver used by bounded and transactional public APIs."""
    return SqlDriver(conn=db_connection)


def format_text_response(text: Any) -> ResponseType:
    """Format MCP text, using compact loss-aware JSON for structured data."""
    rendered = text if isinstance(text, str) else json_text(text)
    return [types.TextContent(type="text", text=rendered)]


def format_error_response(error: str) -> ResponseType:
    """Return a stable human-readable error response."""
    return format_text_response(f"Error: {error}")


@mcp.tool(description="Report the active profile, access policy, and hard execution limits")
async def get_server_capabilities() -> ResponseType:
    """Return deterministic capabilities without a database round trip."""
    return format_text_response(
        {
            "server": "pgsql-mcp",
            "profile": current_profile.value,
            "access_mode": current_access_mode.value,
            "safe_by_default": True,
            "query": {
                "single_statement": True,
                "native_parameters": True,
                "raw_sql_writes": False,
                "default_max_rows": current_max_rows,
                "absolute_max_rows": ABSOLUTE_MAX_ROWS,
                "timeout_seconds": current_query_timeout,
                "read_only_transaction": True,
            },
            "transactions": {
                "available": current_access_mode is AccessMode.UNRESTRICTED,
                "atomic": True,
                "supported_statements": ["select", "insert", "update", "delete", "merge"],
                "write_guards": ["where_required", "max_affected_rows", "expected_rows"],
            },
            "result_encoding": "lossless-tagged-json-fallback",
        }
    )


@mcp.tool(description="List all schemas in the database")
async def list_schemas() -> ResponseType:
    try:
        return format_text_response(await list_schemas_data(await get_sql_driver()))
    except Exception as exc:
        logger.exception("Error listing schemas")
        return format_error_response(str(exc))


@mcp.tool(description="List tables, views, sequences, or extensions")
async def list_objects(
    schema_name: Annotated[str, Field(description="Schema name")],
    object_type: Annotated[str, Field(description="table, view, sequence, or extension")] = "table",
) -> ResponseType:
    try:
        return format_text_response(
            await list_objects_data(
                await get_sql_driver(),
                schema_name=schema_name,
                object_type=object_type,
            )
        )
    except Exception as exc:
        logger.exception("Error listing objects")
        return format_error_response(str(exc))


@mcp.tool(description="Show columns, constraints, indexes, and comments for a database object")
async def get_object_details(
    schema_name: Annotated[str, Field(description="Schema name")],
    object_name: Annotated[str, Field(description="Object name")],
    object_type: Annotated[str, Field(description="table, view, sequence, or extension")] = "table",
) -> ResponseType:
    try:
        return format_text_response(
            await get_object_details_data(
                await get_sql_driver(),
                schema_name=schema_name,
                object_name=object_name,
                object_type=object_type,
            )
        )
    except Exception as exc:
        logger.exception("Error getting object details")
        return format_error_response(str(exc))


@mcp.tool(description="Explain a SQL query and optionally simulate hypothetical indexes")
async def explain_query(
    sql: Annotated[str, Field(description="SQL query to explain")],
    analyze: Annotated[bool, Field(description="Execute the statement to collect actual statistics")] = False,
    hypothetical_indexes: Annotated[
        list[HypotheticalIndex] | None,
        Field(description="Hypothetical index definitions"),
    ] = None,
) -> ResponseType:
    if analyze and current_access_mode is AccessMode.RESTRICTED:
        return format_error_response("EXPLAIN ANALYZE is disabled in restricted mode because it executes the statement")
    try:
        sql_driver = await get_sql_driver()
        await SafeQueryExecutor(
            get_base_sql_driver(),
            timeout_seconds=current_query_timeout,
        ).validate_query(sql, parameter_count=0)
        explain_tool = ExplainPlanTool(sql_driver=sql_driver)
        result: ExplainPlanArtifact | ErrorResult | None
        if hypothetical_indexes:
            if analyze:
                return format_error_response("Cannot use analyze and hypothetical indexes together")
            installed, message = await check_hypopg_installation_status(sql_driver)
            if not installed:
                return format_text_response(message)
            result = await explain_tool.explain_with_hypothetical_indexes(
                sql,
                [index.model_dump() for index in hypothetical_indexes],
            )
        elif analyze:
            result = await explain_tool.explain_analyze(sql)
        else:
            result = await explain_tool.explain(sql)

        if isinstance(result, ExplainPlanArtifact):
            return format_text_response(result.to_text())
        if isinstance(result, ErrorResult):
            return format_error_response(result.to_text())
        return format_error_response("Error processing explain plan")
    except Exception as exc:
        logger.exception("Error explaining query")
        return format_error_response(str(exc))


async def execute_sql(
    sql: Annotated[str, Field(description="Exactly one SQL statement")],
    params: Annotated[list[Any] | None, Field(description="Native values for psycopg %s placeholders")] = None,
    max_rows: Annotated[
        int | None,
        Field(description="Maximum rows returned", ge=1, le=ABSOLUTE_MAX_ROWS),
    ] = None,
) -> ResponseType:
    """Execute exactly one bounded read-only statement."""
    try:
        limits = QueryLimits(
            timeout_seconds=current_query_timeout,
            default_max_rows=current_max_rows,
            absolute_max_rows=ABSOLUTE_MAX_ROWS,
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


async def execute_transaction(
    steps: Annotated[
        list[TransactionStepInput],
        Field(description="Ordered atomic transaction steps", min_length=1),
    ],
    isolation: Annotated[
        Literal["read committed", "repeatable read", "serializable"],
        Field(description="Isolation level"),
    ] = "read committed",
    read_only: Annotated[bool, Field(description="Use a database-enforced read-only transaction")] = False,
    timeout_seconds: Annotated[float, Field(gt=0, le=300)] = DEFAULT_QUERY_TIMEOUT_SECONDS,
    lock_timeout_seconds: Annotated[float, Field(gt=0, le=60)] = DEFAULT_LOCK_TIMEOUT_SECONDS,
) -> ResponseType:
    """Execute guarded steps atomically, rolling back on every failure."""
    if current_access_mode is not AccessMode.UNRESTRICTED:
        return format_error_response("atomic write transactions require --access-mode=unrestricted")
    transaction_steps = [
        TransactionStep(
            sql=step.sql,
            params=tuple(step.params),
            expected_rows=step.expected_rows,
            max_affected_rows=step.max_affected_rows,
            result_mode=ResultMode(step.result_mode),
            max_rows=step.max_rows,
        )
        for step in steps
    ]
    try:
        result = await get_base_sql_driver().execute_transaction(
            transaction_steps,
            isolation=IsolationLevel(isolation),
            read_only=read_only,
            timeout_seconds=timeout_seconds,
            lock_timeout_seconds=lock_timeout_seconds,
            absolute_max_rows=ABSOLUTE_MAX_ROWS,
        )
        return format_text_response(result.to_payload())
    except TransactionExecutionError as exc:
        return format_text_response(exc.to_payload())
    except (TransactionValidationError, ValueError) as exc:
        return format_error_response(str(exc))
    except Exception as exc:
        logger.exception("Unexpected transaction error")
        return format_error_response(str(exc))


@mcp.tool(description="Analyze frequently executed queries and recommend indexes")
async def analyze_workload_indexes(
    max_index_size_mb: int = 10_000,
    method: Literal["dta", "llm"] = "dta",
) -> ResponseType:
    try:
        from .index.dta_calc import DatabaseTuningAdvisor
        from .index.presentation import TextPresentation

        sql_driver = await get_sql_driver()
        if method == "dta":
            optimizer = DatabaseTuningAdvisor(sql_driver)
        else:
            try:
                from .index.llm_opt import LLMOptimizerTool
            except ImportError:
                return format_error_response("LLM index analysis dependencies are not installed")
            optimizer = LLMOptimizerTool(sql_driver)
        result = await TextPresentation(sql_driver, optimizer).analyze_workload(max_index_size_mb=max_index_size_mb)
        return format_text_response(result)
    except Exception as exc:
        logger.exception("Error analyzing workload")
        return format_error_response(str(exc))


@mcp.tool(description="Analyze up to ten SQL queries and recommend indexes")
async def analyze_query_indexes(
    queries: list[str],
    max_index_size_mb: int = 10_000,
    method: Literal["dta", "llm"] = "dta",
) -> ResponseType:
    if not queries:
        return format_error_response("Please provide a non-empty list of queries to analyze.")
    if len(queries) > MAX_NUM_INDEX_TUNING_QUERIES:
        return format_error_response(f"Please provide up to {MAX_NUM_INDEX_TUNING_QUERIES} queries.")
    try:
        from .index.dta_calc import DatabaseTuningAdvisor
        from .index.presentation import TextPresentation

        sql_driver = await get_sql_driver()
        if method == "dta":
            optimizer = DatabaseTuningAdvisor(sql_driver)
        else:
            try:
                from .index.llm_opt import LLMOptimizerTool
            except ImportError:
                return format_error_response("LLM index analysis dependencies are not installed")
            optimizer = LLMOptimizerTool(sql_driver)
        result = await TextPresentation(sql_driver, optimizer).analyze_queries(
            queries=queries,
            max_index_size_mb=max_index_size_mb,
        )
        return format_text_response(result)
    except Exception as exc:
        logger.exception("Error analyzing queries")
        return format_error_response(str(exc))


@mcp.tool(
    description=(
        "Analyze database health. Valid values: index, connection, vacuum, sequence, "
        "replication, buffer, constraint, all; comma-separated values are accepted."
    )
)
async def analyze_db_health(health_type: str = "all") -> ResponseType:
    try:
        from .database_health import DatabaseHealthTool

        result = await DatabaseHealthTool(await get_sql_driver()).health(health_type=health_type)
        return format_text_response(result)
    except Exception as exc:
        logger.exception("Error analyzing database health")
        return format_error_response(str(exc))


@mcp.tool(
    name="get_top_queries",
    description=f"Report slow or resource-intensive queries using {PG_STAT_STATEMENTS}",
)
async def get_top_queries(sort_by: str = "resources", limit: int = 10) -> ResponseType:
    try:
        from .top_queries import TopQueriesCalc

        calculator = TopQueriesCalc(sql_driver=await get_sql_driver())
        if sort_by == "resources":
            return format_text_response(await calculator.get_top_resource_queries())
        if sort_by in ("mean_time", "total_time"):
            result = await calculator.get_top_queries_by_time(
                limit=limit,
                sort_by="mean" if sort_by == "mean_time" else "total",
            )
            return format_text_response(result)
        return format_error_response("Invalid sort criteria. Use resources, mean_time, or total_time.")
    except Exception as exc:
        logger.exception("Error getting top queries")
        return format_error_response(str(exc))


def build_argument_parser() -> argparse.ArgumentParser:
    """Build the CLI parser so defaults can be tested without starting MCP."""
    parser = argparse.ArgumentParser(description="PostgreSQL MCP Server")
    parser.add_argument("database_url", help="Database connection URL", nargs="?")
    parser.add_argument(
        "--access-mode",
        choices=[mode.value for mode in AccessMode],
        default=AccessMode.RESTRICTED.value,
        help="restricted (default, read-only) or unrestricted (guarded writes enabled)",
    )
    parser.add_argument("--transport", choices=["stdio", "sse"], default="stdio")
    parser.add_argument("--sse-host", default=None)
    parser.add_argument("--sse-port", type=int, default=None)
    parser.add_argument("--sse-path", default=None)
    parser.add_argument("--cors-allow-origins", default=None)
    parser.add_argument("--query-timeout", type=float, default=None)
    parser.add_argument("--max-rows", type=int, default=None)
    return parser


async def main() -> None:
    """Configure one database profile and run the selected MCP transport."""
    args = build_argument_parser().parse_args()

    global current_access_mode
    global current_max_rows
    global current_query_timeout
    current_access_mode = AccessMode(args.access_mode)
    current_query_timeout = (
        args.query_timeout if args.query_timeout is not None else float(env_number("QUERY_TIMEOUT", DEFAULT_QUERY_TIMEOUT_SECONDS, float))
    )
    current_max_rows = args.max_rows if args.max_rows is not None else int(env_number("MAX_ROWS", DEFAULT_MAX_ROWS, int))
    QueryLimits(
        timeout_seconds=current_query_timeout,
        default_max_rows=current_max_rows,
        absolute_max_rows=ABSOLUTE_MAX_ROWS,
    ).validate()

    mcp.add_tool(
        execute_sql,
        description="Execute exactly one read-only, parameterized SQL statement with bounded results",
    )
    if current_access_mode is AccessMode.UNRESTRICTED:
        mcp.add_tool(
            execute_transaction,
            description="Execute guarded SQL steps atomically; every failure rolls back the transaction",
        )

    logger.info(
        "Starting PostgreSQL MCP Server profile=%s access_mode=%s",
        current_profile.value,
        current_access_mode.value,
    )
    database_url = os.environ.get("DATABASE_URI", args.database_url)
    if not database_url:
        raise ValueError("No database URL provided. Set DATABASE_URI or pass the positional database_url.")

    try:
        await db_connection.pool_connect(database_url)
        logger.info("Connected to PostgreSQL")
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
    """Close database resources and terminate with a signal-compatible status."""
    global shutdown_in_progress
    if shutdown_in_progress:
        logger.warning("Forcing immediate exit")
        raise SystemExit(1)
    shutdown_in_progress = True
    if sig:
        logger.info("Received exit signal %s", sig.name)
    try:
        await db_connection.close()
    except Exception:
        logger.exception("Error closing database connections")
    raise SystemExit(128 + int(sig) if sig is not None else 0)
