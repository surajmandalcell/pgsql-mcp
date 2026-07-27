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
from .catalog_advanced import get_postgres_type_data  # noqa: E402
from .catalog_advanced import get_relation_details_data  # noqa: E402
from .catalog_advanced import get_server_info_data  # noqa: E402
from .catalog_advanced import list_postgres_types_data  # noqa: E402
from .catalog_advanced import list_relations_data  # noqa: E402
from .catalog_advanced import search_catalog_data  # noqa: E402
from .data_ops import MAX_DATA_RESULT_BYTES  # noqa: E402
from .data_ops import MAX_DATA_ROWS  # noqa: E402
from .data_ops import ComparisonOperator  # noqa: E402
from .data_ops import DataOperationError  # noqa: E402
from .data_ops import DataService  # noqa: E402
from .data_ops import DeleteRowsRequest  # noqa: E402
from .data_ops import FilterCondition  # noqa: E402
from .data_ops import FilterSet  # noqa: E402
from .data_ops import InsertRowsRequest  # noqa: E402
from .data_ops import MutationGuard  # noqa: E402
from .data_ops import OrderDirection  # noqa: E402
from .data_ops import OrderTerm  # noqa: E402
from .data_ops import PostgresDataRepository  # noqa: E402
from .data_ops import QualifiedRelation  # noqa: E402
from .data_ops import SelectRowsRequest  # noqa: E402
from .data_ops import UpdateRowsRequest  # noqa: E402
from .data_ops import UpsertRowsRequest  # noqa: E402
from .explain import ExplainPlanTool  # noqa: E402
from .extension_profiles import ExtensionProfileError  # noqa: E402
from .extension_profiles import PostgresExtensionProfileRepository  # noqa: E402
from .maintenance import MaintenanceError  # noqa: E402
from .maintenance import MaintenanceExecutionError  # noqa: E402
from .maintenance import MaintenanceOperation  # noqa: E402
from .maintenance import MaintenanceOptions  # noqa: E402
from .maintenance import MaintenanceRequest  # noqa: E402
from .maintenance import MaintenanceService  # noqa: E402
from .maintenance import MaintenanceTarget  # noqa: E402
from .maintenance import PostgresMaintenanceBackend  # noqa: E402
from .maintenance import ReconciliationResolution  # noqa: E402
from .migrations import MigrationError  # noqa: E402
from .migrations import MigrationExecutionError  # noqa: E402
from .migrations import MigrationPlanner  # noqa: E402
from .migrations import MigrationService  # noqa: E402
from .migrations import MigrationStepDraft  # noqa: E402
from .migrations import PostgresMigrationBackend  # noqa: E402
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


class MigrationStepInput(BaseModel):
    """One reviewed forward DDL statement and its compensating statement."""

    sql: str = Field(description="Exactly one PostgreSQL migration statement")
    rollback_sql: str = Field(description="Exactly one compensating PostgreSQL statement")


class FilterConditionInput(BaseModel):
    """One typed value-bound data filter."""

    column: str = Field(description="Exact column name")
    operator: Literal["eq", "ne", "lt", "lte", "gt", "gte", "in", "not_in", "like", "ilike", "is_null", "is_not_null"]
    value: Any = Field(default=None, description="Bound value; omit for null operators")


class FilterSetInput(BaseModel):
    """A bounded `(all predicates) AND (any predicates)` filter."""

    all: list[FilterConditionInput] = Field(default_factory=list, description="Predicates combined with AND")
    any: list[FilterConditionInput] = Field(default_factory=list, description="Predicates combined with OR")


class OrderTermInput(BaseModel):
    """One stable keyset ordering term."""

    column: str = Field(description="Exact NOT NULL order column")
    direction: Literal["asc", "desc"] = Field(default="asc")


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
current_migration_schema = "public"
current_maintenance_schema = "public"
migration_planner = MigrationPlanner()
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


def get_extension_profile_repository() -> PostgresExtensionProfileRepository:
    """Build the bounded read-only extension inventory repository."""
    return PostgresExtensionProfileRepository(
        get_base_sql_driver(),
        timeout_seconds=max(1.0, float(current_query_timeout)),
    )


def get_migration_service() -> MigrationService:
    """Build the reviewed migration application service for this database."""
    return MigrationService(PostgresMigrationBackend(get_base_sql_driver(), ledger_schema=current_migration_schema))


def get_maintenance_service() -> MaintenanceService:
    """Build the reviewed nontransactional maintenance service."""
    return MaintenanceService(
        PostgresMaintenanceBackend(
            get_base_sql_driver(),
            ledger_schema=current_maintenance_schema,
            inspection_timeout_seconds=max(1, int(current_query_timeout)),
        )
    )


def get_data_service() -> DataService:
    """Build the structured data-operations application service."""
    return DataService(
        PostgresDataRepository(
            get_base_sql_driver(),
            timeout_seconds=current_query_timeout,
            lock_timeout_seconds=min(current_query_timeout, DEFAULT_LOCK_TIMEOUT_SECONDS),
        )
    )


def _filter_set(value: FilterSetInput | None) -> FilterSet:
    if value is None:
        return FilterSet()

    def condition(item: FilterConditionInput) -> FilterCondition:
        operator = ComparisonOperator(item.operator)
        if operator in {ComparisonOperator.IS_NULL, ComparisonOperator.IS_NOT_NULL}:
            return FilterCondition(item.column, operator)
        return FilterCondition(item.column, operator, item.value)

    return FilterSet(
        all_of=tuple(condition(item) for item in value.all),
        any_of=tuple(condition(item) for item in value.any),
    )


def _maintenance_request(
    *,
    name: str,
    operation: str,
    schema_name: str,
    target_name: str,
    skip_locked: bool,
    index_cleanup: str,
    parallel: int,
) -> MaintenanceRequest:
    return MaintenanceRequest(
        name=name,
        operation=MaintenanceOperation(operation),
        target=MaintenanceTarget(schema_name, target_name),
        options=MaintenanceOptions(
            skip_locked=skip_locked,
            index_cleanup=index_cleanup,
            parallel=parallel,
        ),
    )


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
            "catalog": {
                "oid_backed": True,
                "relations": True,
                "routines": True,
                "partitions": True,
                "policies": True,
                "privileges": True,
            },
            "postgres_types": {
                "dynamic": True,
                "supported_kinds": ["array", "base", "composite", "domain", "enum", "multirange", "pseudo", "range"],
                "unknown_extension_types": "preserved_with_oid_and_tagged_value",
            },
            "extensions": {
                "dynamic_inventory": True,
                "unknown_extensions": "preserved_as_generic_catalog_profiles",
                "known_families": ["postgis", "timescaledb", "citus", "pgvector", "hypopg", "pg_stat_statements"],
                "catalog_and_type_compatible": ["postgis", "timescaledb", "citus", "pgvector"],
                "specialized_tools": ["hypopg", "pg_stat_statements"],
            },
            "migrations": {
                "planning": True,
                "apply_available": current_access_mode is AccessMode.UNRESTRICTED,
                "review_hash_required": True,
                "atomic_ledger": True,
                "canonical_plan_ledger": True,
                "rollback_policy_revalidated": True,
                "ambiguous_commit_state_reported": True,
                "non_transactional_apply": False,
            },
            "maintenance": {
                "planning": True,
                "apply_available": current_access_mode is AccessMode.UNRESTRICTED,
                "review_hash_required": True,
                "transaction_behavior": "non_transactional",
                "rollback_available": False,
                "durable_status": ["running", "succeeded", "failed", "unknown"],
                "unknown_outcome_reconciliation": True,
                "supported_operations": [
                    "vacuum_analyze",
                    "analyze",
                    "reindex_index_concurrently",
                    "refresh_materialized_view_concurrently",
                ],
            },
            "data_operations": {
                "structured_filters": True,
                "identifier_composition": "psycopg.sql.Identifier",
                "keyset_pagination": True,
                "select_available": True,
                "mutations_available": current_access_mode is AccessMode.UNRESTRICTED,
                "max_rows": MAX_DATA_ROWS,
                "max_result_bytes": MAX_DATA_RESULT_BYTES,
                "write_guards": ["max_affected_rows", "expected_rows", "optimistic_concurrency"],
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


@mcp.tool(description="Report PostgreSQL version, database, role, recovery, locale, and installed extensions")
async def get_server_info() -> ResponseType:
    """Return server metadata from trusted, bounded catalog queries."""
    try:
        return format_text_response(await get_server_info_data(get_base_sql_driver()))
    except Exception as exc:
        logger.exception("Error getting server information")
        return format_error_response(str(exc))


@mcp.tool(description="List installed or available PostgreSQL extension capability profiles")
async def get_extension_profiles(
    include_available: Annotated[
        bool,
        Field(description="Include extensions available to install but not currently installed"),
    ] = False,
) -> ResponseType:
    """Return a bounded, read-only extension support inventory."""
    try:
        snapshot = await get_extension_profile_repository().snapshot(include_available=include_available)
        return format_text_response(snapshot.to_payload())
    except ExtensionProfileError as exc:
        return format_error_response(str(exc))
    except Exception as exc:
        logger.exception("Unexpected extension profile error")
        return format_error_response(str(exc))


@mcp.tool(description="Search relations, routines, types, collations, and extensions")
async def search_catalog(
    term: Annotated[str, Field(description="Case-insensitive name or comment fragment")],
    schema_name: Annotated[str | None, Field(description="Optional exact schema filter")] = None,
    object_kind: Annotated[str | None, Field(description="Optional exact object-kind filter")] = None,
    include_system: Annotated[bool, Field(description="Include pg_catalog and other system schemas")] = False,
    limit: Annotated[int, Field(description="Maximum matches", ge=1, le=500)] = 100,
    offset: Annotated[int, Field(description="Result offset", ge=0)] = 0,
) -> ResponseType:
    """Search the PostgreSQL catalog without executing user-provided SQL."""
    try:
        return format_text_response(
            await search_catalog_data(
                get_base_sql_driver(),
                term=term,
                schema_name=schema_name,
                object_kind=object_kind,
                include_system=include_system,
                limit=limit,
                offset=offset,
            )
        )
    except Exception as exc:
        logger.exception("Error searching PostgreSQL catalog")
        return format_error_response(str(exc))


@mcp.tool(description="List tables, partitions, views, sequences, foreign tables, indexes, and other relations")
async def list_relations(
    schema_name: Annotated[str | None, Field(description="Optional exact schema filter")] = None,
    relation_kind: Annotated[
        str | None,
        Field(
            description=(
                "Optional kind: table, partitioned_table, view, materialized_view, sequence, "
                "foreign_table, index, partitioned_index, composite, or toast"
            )
        ),
    ] = None,
    include_system: Annotated[bool, Field(description="Include system schemas")] = False,
    limit: Annotated[int, Field(description="Maximum relations", ge=1, le=500)] = 100,
    offset: Annotated[int, Field(description="Result offset", ge=0)] = 0,
) -> ResponseType:
    """List relation classes with ownership, storage, partition, and RLS metadata."""
    try:
        return format_text_response(
            await list_relations_data(
                get_base_sql_driver(),
                schema_name=schema_name,
                relation_kind=relation_kind,
                include_system=include_system,
                limit=limit,
                offset=offset,
            )
        )
    except Exception as exc:
        logger.exception("Error listing PostgreSQL relations")
        return format_error_response(str(exc))


@mcp.tool(description="Inspect a relation's columns, constraints, indexes, triggers, policies, partitions, and privileges")
async def get_relation_details(
    schema_name: Annotated[str, Field(description="Exact schema name")],
    relation_name: Annotated[str, Field(description="Exact relation name")],
) -> ResponseType:
    """Return complete relation metadata while preserving PostgreSQL type OIDs."""
    try:
        return format_text_response(
            await get_relation_details_data(
                get_base_sql_driver(),
                schema_name=schema_name,
                relation_name=relation_name,
            )
        )
    except Exception as exc:
        logger.exception("Error getting PostgreSQL relation details")
        return format_error_response(str(exc))


@mcp.tool(description="List built-in, user-defined, and extension-owned PostgreSQL types by OID")
async def list_postgres_types(
    schema_name: Annotated[str | None, Field(description="Optional exact schema filter")] = None,
    type_kind: Annotated[
        str | None,
        Field(description="Optional kind: array, base, composite, domain, enum, multirange, pseudo, or range"),
    ] = None,
    include_system: Annotated[bool, Field(description="Include system types")] = False,
    limit: Annotated[int, Field(description="Maximum types", ge=1, le=500)] = 100,
    offset: Annotated[int, Field(description="Result offset", ge=0)] = 0,
) -> ResponseType:
    """List every PostgreSQL type family through the live system catalog."""
    try:
        return format_text_response(
            await list_postgres_types_data(
                get_base_sql_driver(),
                schema_name=schema_name,
                type_kind=type_kind,
                include_system=include_system,
                limit=limit,
                offset=offset,
            )
        )
    except Exception as exc:
        logger.exception("Error listing PostgreSQL types")
        return format_error_response(str(exc))


@mcp.tool(description="Inspect any PostgreSQL type, including enum, domain, composite, range, multirange, array, and extension types")
async def get_postgres_type(
    type_oid: Annotated[int | None, Field(description="Exact PostgreSQL type OID", ge=1)] = None,
    schema_name: Annotated[str | None, Field(description="Type schema when OID is omitted")] = None,
    type_name: Annotated[str | None, Field(description="Type name when OID is omitted")] = None,
) -> ResponseType:
    """Return dynamic OID-backed metadata for one PostgreSQL type."""
    try:
        return format_text_response(
            await get_postgres_type_data(
                get_base_sql_driver(),
                type_oid=type_oid,
                schema_name=schema_name,
                type_name=type_name,
            )
        )
    except Exception as exc:
        logger.exception("Error getting PostgreSQL type details")
        return format_error_response(str(exc))


@mcp.tool(description="Select bounded rows using catalog-validated identifiers, typed filters, and keyset pagination")
async def select_rows(
    schema_name: Annotated[str, Field(description="Exact schema name")],
    relation_name: Annotated[str, Field(description="Exact table or readable relation name")],
    columns: Annotated[list[str] | None, Field(description="Projected columns; omit to select every column")] = None,
    where: Annotated[FilterSetInput | None, Field(description="Structured value-bound filter")] = None,
    order_by: Annotated[list[OrderTermInput] | None, Field(description="Stable order including a primary or unique key")] = None,
    limit: Annotated[int, Field(description="Maximum visible rows", ge=1, le=MAX_DATA_ROWS)] = 100,
    cursor: Annotated[str | None, Field(description="Opaque keyset cursor from a prior page")] = None,
) -> ResponseType:
    """Read through the structured data boundary; available in every access mode."""
    try:
        request = SelectRowsRequest(
            relation=QualifiedRelation(schema_name, relation_name),
            columns=tuple(columns or ()),
            filters=_filter_set(where),
            order_by=tuple(OrderTerm(item.column, OrderDirection(item.direction)) for item in (order_by or ())),
            limit=limit,
            cursor=cursor,
        )
        return format_text_response((await get_data_service().select(request)).to_payload())
    except (DataOperationError, ValueError) as exc:
        logger.exception("Error selecting structured rows")
        return format_error_response(str(exc))


@mcp.tool(description="Insert bounded typed rows with exact affected-row commit guards")
async def insert_rows(
    schema_name: Annotated[str, Field(description="Exact schema name")],
    relation_name: Annotated[str, Field(description="Exact table name")],
    rows: Annotated[list[dict[str, Any]], Field(description="Rows sharing one column set", min_length=1, max_length=MAX_DATA_ROWS)],
    returning: Annotated[list[str] | None, Field(description="Columns returned after commit validation")] = None,
    max_affected_rows: Annotated[int, Field(description="Hard mutation ceiling", ge=1, le=MAX_DATA_ROWS)] = 1,
    expected_rows: Annotated[int | None, Field(description="Optional exact affected row count", ge=0)] = None,
) -> ResponseType:
    if current_access_mode is not AccessMode.UNRESTRICTED:
        return format_error_response("insert_rows requires unrestricted mode")
    try:
        request = InsertRowsRequest(
            relation=QualifiedRelation(schema_name, relation_name),
            rows=tuple(rows),
            returning=tuple(returning or ()),
            guard=MutationGuard(max_affected_rows, expected_rows),
        )
        return format_text_response((await get_data_service().insert(request)).to_payload())
    except (DataOperationError, ValueError) as exc:
        logger.exception("Error inserting structured rows")
        return format_error_response(str(exc))


@mcp.tool(description="Upsert typed rows through a verified primary or unique conflict key")
async def upsert_rows(
    schema_name: Annotated[str, Field(description="Exact schema name")],
    relation_name: Annotated[str, Field(description="Exact table name")],
    rows: Annotated[list[dict[str, Any]], Field(description="Rows sharing one column set", min_length=1, max_length=MAX_DATA_ROWS)],
    conflict_columns: Annotated[list[str], Field(description="Complete primary or non-partial unique key", min_length=1)],
    update_columns: Annotated[list[str] | None, Field(description="Inserted columns updated on conflict")] = None,
    returning: Annotated[list[str] | None, Field(description="Columns returned after commit validation")] = None,
    max_affected_rows: Annotated[int, Field(description="Hard mutation ceiling", ge=1, le=MAX_DATA_ROWS)] = 1,
    expected_rows: Annotated[int | None, Field(description="Optional exact affected row count", ge=0)] = None,
) -> ResponseType:
    if current_access_mode is not AccessMode.UNRESTRICTED:
        return format_error_response("upsert_rows requires unrestricted mode")
    try:
        request = UpsertRowsRequest(
            relation=QualifiedRelation(schema_name, relation_name),
            rows=tuple(rows),
            conflict_columns=tuple(conflict_columns),
            update_columns=tuple(update_columns or ()),
            returning=tuple(returning or ()),
            guard=MutationGuard(max_affected_rows, expected_rows),
        )
        return format_text_response((await get_data_service().upsert(request)).to_payload())
    except (DataOperationError, ValueError) as exc:
        logger.exception("Error upserting structured rows")
        return format_error_response(str(exc))


@mcp.tool(description="Update typed values with mandatory filters, optimistic predicates, and commit guards")
async def update_rows(
    schema_name: Annotated[str, Field(description="Exact schema name")],
    relation_name: Annotated[str, Field(description="Exact table name")],
    values: Annotated[dict[str, Any], Field(description="Columns and bound replacement values")],
    where: Annotated[FilterSetInput, Field(description="Mandatory structured target filter")],
    concurrency: Annotated[FilterSetInput | None, Field(description="Optional optimistic concurrency predicates")] = None,
    returning: Annotated[list[str] | None, Field(description="Columns returned after commit validation")] = None,
    max_affected_rows: Annotated[int, Field(description="Hard mutation ceiling", ge=1, le=MAX_DATA_ROWS)] = 1,
    expected_rows: Annotated[int | None, Field(description="Optional exact affected row count", ge=0)] = None,
) -> ResponseType:
    if current_access_mode is not AccessMode.UNRESTRICTED:
        return format_error_response("update_rows requires unrestricted mode")
    try:
        request = UpdateRowsRequest(
            relation=QualifiedRelation(schema_name, relation_name),
            values=values,
            filters=_filter_set(where),
            concurrency=_filter_set(concurrency),
            returning=tuple(returning or ()),
            guard=MutationGuard(max_affected_rows, expected_rows),
        )
        return format_text_response((await get_data_service().update(request)).to_payload())
    except (DataOperationError, ValueError) as exc:
        logger.exception("Error updating structured rows")
        return format_error_response(str(exc))


@mcp.tool(description="Delete rows with mandatory filters, optimistic predicates, and commit guards")
async def delete_rows(
    schema_name: Annotated[str, Field(description="Exact schema name")],
    relation_name: Annotated[str, Field(description="Exact table name")],
    where: Annotated[FilterSetInput, Field(description="Mandatory structured target filter")],
    concurrency: Annotated[FilterSetInput | None, Field(description="Optional optimistic concurrency predicates")] = None,
    returning: Annotated[list[str] | None, Field(description="Columns returned after commit validation")] = None,
    max_affected_rows: Annotated[int, Field(description="Hard mutation ceiling", ge=1, le=MAX_DATA_ROWS)] = 1,
    expected_rows: Annotated[int | None, Field(description="Optional exact affected row count", ge=0)] = None,
) -> ResponseType:
    if current_access_mode is not AccessMode.UNRESTRICTED:
        return format_error_response("delete_rows requires unrestricted mode")
    try:
        request = DeleteRowsRequest(
            relation=QualifiedRelation(schema_name, relation_name),
            filters=_filter_set(where),
            concurrency=_filter_set(concurrency),
            returning=tuple(returning or ()),
            guard=MutationGuard(max_affected_rows, expected_rows),
        )
        return format_text_response((await get_data_service().delete(request)).to_payload())
    except (DataOperationError, ValueError) as exc:
        logger.exception("Error deleting structured rows")
        return format_error_response(str(exc))


@mcp.tool(description="Create a deterministic reviewed PostgreSQL migration plan without touching the database")
async def create_migration_plan(
    name: Annotated[str, Field(description="Stable migration name")],
    steps: Annotated[list[MigrationStepInput], Field(description="Ordered forward and rollback statement pairs", min_length=1)],
) -> ResponseType:
    """Parse, classify, and hash a migration for human review."""
    try:
        plan = migration_planner.create_plan(
            name=name,
            steps=[MigrationStepDraft(step.sql, step.rollback_sql) for step in steps],
        )
        return format_text_response(plan.to_payload())
    except (MigrationError, ValueError) as exc:
        return format_error_response(str(exc))
    except Exception as exc:
        logger.exception("Unexpected migration planning error")
        return format_error_response(str(exc))


@mcp.tool(description="Apply a reviewed fully transactional migration and its ledger row atomically")
async def apply_migration_plan(
    name: Annotated[str, Field(description="Stable migration name")],
    steps: Annotated[list[MigrationStepInput], Field(description="The exact reviewed ordered statement pairs", min_length=1)],
    review_hash: Annotated[str, Field(description="Exact 64-character review hash from create_migration_plan")],
    timeout_seconds: Annotated[int, Field(ge=1, le=900)] = 30,
    lock_timeout_seconds: Annotated[int, Field(ge=1, le=300)] = 5,
) -> ResponseType:
    """Rebuild the reviewed aggregate and execute it on one transaction."""
    if current_access_mode is not AccessMode.UNRESTRICTED:
        return format_error_response("reviewed migration apply requires --access-mode=unrestricted")
    try:
        plan = migration_planner.create_plan(
            name=name,
            steps=[MigrationStepDraft(step.sql, step.rollback_sql) for step in steps],
        )
        result = await get_migration_service().apply(
            plan,
            review_hash=review_hash,
            timeout_seconds=timeout_seconds,
            lock_timeout_seconds=lock_timeout_seconds,
        )
        return format_text_response(result.to_payload())
    except MigrationExecutionError as exc:
        return format_text_response(exc.to_payload())
    except (MigrationError, ValueError) as exc:
        return format_error_response(str(exc))
    except Exception as exc:
        logger.exception("Unexpected migration apply error")
        return format_error_response(str(exc))


@mcp.tool(description="List redacted reviewed migration ledger metadata")
async def get_migration_status(
    limit: Annotated[int, Field(description="Maximum migration rows", ge=1, le=500)] = 100,
) -> ResponseType:
    """Return bounded migration history without stored SQL text."""
    try:
        snapshot = await get_migration_service().status(limit=limit)
        return format_text_response(snapshot.to_payload())
    except (MigrationError, ValueError) as exc:
        return format_error_response(str(exc))
    except Exception as exc:
        logger.exception("Unexpected migration status error")
        return format_error_response(str(exc))


@mcp.tool(description="Atomically roll back the latest reviewed migration using its stored verified plan")
async def rollback_migration(
    name: Annotated[str, Field(description="Applied migration name")],
    review_hash: Annotated[str, Field(description="Exact review hash of the stored plan")],
    timeout_seconds: Annotated[int, Field(ge=1, le=900)] = 30,
    lock_timeout_seconds: Annotated[int, Field(ge=1, le=300)] = 5,
) -> ResponseType:
    """Execute stored compensating statements in reverse order on one transaction."""
    if current_access_mode is not AccessMode.UNRESTRICTED:
        return format_error_response("reviewed migration rollback requires --access-mode=unrestricted")
    try:
        result = await get_migration_service().rollback(
            name=name,
            review_hash=review_hash,
            timeout_seconds=timeout_seconds,
            lock_timeout_seconds=lock_timeout_seconds,
        )
        return format_text_response(result.to_payload())
    except MigrationExecutionError as exc:
        return format_text_response(exc.to_payload())
    except (MigrationError, ValueError) as exc:
        return format_error_response(str(exc))
    except Exception as exc:
        logger.exception("Unexpected migration rollback error")
        return format_error_response(str(exc))


@mcp.tool(description="Create a reviewed nontransactional PostgreSQL maintenance plan")
async def create_maintenance_plan(
    name: Annotated[str, Field(description="Stable maintenance operation name")],
    operation: Annotated[
        Literal[
            "vacuum_analyze",
            "analyze",
            "reindex_index_concurrently",
            "refresh_materialized_view_concurrently",
        ],
        Field(description="Structured maintenance operation"),
    ],
    schema_name: Annotated[str, Field(description="Exact target schema")],
    target_name: Annotated[str, Field(description="Exact relation or index name")],
    skip_locked: Annotated[bool, Field(description="Skip relations that cannot be locked immediately")] = False,
    index_cleanup: Annotated[Literal["auto", "on", "off"], Field(description="VACUUM index cleanup policy")] = "auto",
    parallel: Annotated[int, Field(description="VACUUM parallel workers", ge=0, le=1024)] = 0,
) -> ResponseType:
    try:
        request = _maintenance_request(
            name=name,
            operation=operation,
            schema_name=schema_name,
            target_name=target_name,
            skip_locked=skip_locked,
            index_cleanup=index_cleanup,
            parallel=parallel,
        )
        return format_text_response((await get_maintenance_service().plan(request)).to_payload())
    except (MaintenanceError, ValueError) as exc:
        return format_error_response(str(exc))
    except Exception as exc:
        logger.exception("Unexpected maintenance planning error")
        return format_error_response(str(exc))


@mcp.tool(description="Apply an exact reviewed nontransactional maintenance plan")
async def apply_maintenance_plan(
    name: Annotated[str, Field(description="Stable maintenance operation name")],
    operation: Annotated[
        Literal[
            "vacuum_analyze",
            "analyze",
            "reindex_index_concurrently",
            "refresh_materialized_view_concurrently",
        ],
        Field(description="Structured maintenance operation"),
    ],
    schema_name: Annotated[str, Field(description="Exact target schema")],
    target_name: Annotated[str, Field(description="Exact relation or index name")],
    review_hash: Annotated[str, Field(description="Exact review hash returned by create_maintenance_plan")],
    skip_locked: Annotated[bool, Field(description="Skip relations that cannot be locked immediately")] = False,
    index_cleanup: Annotated[Literal["auto", "on", "off"], Field(description="VACUUM index cleanup policy")] = "auto",
    parallel: Annotated[int, Field(description="VACUUM parallel workers", ge=0, le=1024)] = 0,
    timeout_seconds: Annotated[int, Field(ge=1, le=7200)] = 300,
    lock_timeout_seconds: Annotated[int, Field(ge=1, le=300)] = 5,
) -> ResponseType:
    if current_access_mode is not AccessMode.UNRESTRICTED:
        return format_error_response("reviewed maintenance apply requires --access-mode=unrestricted")
    try:
        request = _maintenance_request(
            name=name,
            operation=operation,
            schema_name=schema_name,
            target_name=target_name,
            skip_locked=skip_locked,
            index_cleanup=index_cleanup,
            parallel=parallel,
        )
        service = get_maintenance_service()
        plan = await service.plan(request)
        result = await service.apply(
            plan,
            review_hash=review_hash,
            timeout_seconds=timeout_seconds,
            lock_timeout_seconds=lock_timeout_seconds,
        )
        return format_text_response(result.to_payload())
    except MaintenanceExecutionError as exc:
        return format_text_response(exc.to_payload())
    except (MaintenanceError, ValueError) as exc:
        return format_error_response(str(exc))
    except Exception as exc:
        logger.exception("Unexpected maintenance apply error")
        return format_error_response(str(exc))


@mcp.tool(description="List redacted reviewed maintenance status records")
async def get_maintenance_status(
    limit: Annotated[int, Field(description="Maximum status rows", ge=1, le=500)] = 100,
) -> ResponseType:
    try:
        return format_text_response((await get_maintenance_service().status(limit=limit)).to_payload())
    except (MaintenanceError, ValueError) as exc:
        return format_error_response(str(exc))
    except Exception as exc:
        logger.exception("Unexpected maintenance status error")
        return format_error_response(str(exc))


@mcp.tool(description="Reconcile a reviewed maintenance operation after external outcome verification")
async def reconcile_maintenance_operation(
    name: Annotated[str, Field(description="Stable maintenance operation name")],
    review_hash: Annotated[str, Field(description="Exact stored review hash")],
    resolution: Annotated[Literal["succeeded", "failed"], Field(description="Externally verified outcome")],
) -> ResponseType:
    if current_access_mode is not AccessMode.UNRESTRICTED:
        return format_error_response("maintenance reconciliation requires --access-mode=unrestricted")
    try:
        result = await get_maintenance_service().reconcile(
            name=name,
            review_hash=review_hash,
            resolution=ReconciliationResolution(resolution),
        )
        return format_text_response(result.to_payload())
    except MaintenanceExecutionError as exc:
        return format_text_response(exc.to_payload())
    except (MaintenanceError, ValueError) as exc:
        return format_error_response(str(exc))
    except Exception as exc:
        logger.exception("Unexpected maintenance reconciliation error")
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
    parser.add_argument(
        "--migration-schema",
        default=os.environ.get("MIGRATION_SCHEMA", "public"),
        help="Existing schema that owns the trusted reviewed-migration ledger",
    )
    parser.add_argument(
        "--maintenance-schema",
        default=os.environ.get("MAINTENANCE_SCHEMA", "public"),
        help="Existing schema that owns the trusted reviewed-maintenance ledger",
    )
    return parser


async def main() -> None:
    """Configure one database profile and run the selected MCP transport."""
    args = build_argument_parser().parse_args()

    global current_access_mode
    global current_max_rows
    global current_maintenance_schema
    global current_migration_schema
    global current_query_timeout
    current_access_mode = AccessMode(args.access_mode)
    current_query_timeout = (
        args.query_timeout if args.query_timeout is not None else float(env_number("QUERY_TIMEOUT", DEFAULT_QUERY_TIMEOUT_SECONDS, float))
    )
    current_max_rows = args.max_rows if args.max_rows is not None else int(env_number("MAX_ROWS", DEFAULT_MAX_ROWS, int))
    current_migration_schema = args.migration_schema
    current_maintenance_schema = args.maintenance_schema
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
