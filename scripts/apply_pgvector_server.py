from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    source = target.read_text()
    if source.count(old) != 1:
        raise RuntimeError(f"expected one marker in {path}: {old[:100]!r}")
    target.write_text(source.replace(old, new, 1))


replace_once(
    "src/postgres_mcp/pgvector_diagnostics.py",
    "member.objid = ANY(index.indclass))",
    "member.objid = ANY(index.indclass::oid[]))",
)

server = "src/postgres_mcp/server.py"
replace_once(
    server,
    "from .provider_profiles import ProviderProfileError  # noqa: E402\n",
    "from .provider_profiles import ProviderProfileError  # noqa: E402\n"
    "from .pgvector_diagnostics import MAX_PGVECTOR_ITEMS  # noqa: E402\n"
    "from .pgvector_diagnostics import PgvectorCatalogError  # noqa: E402\n"
    "from .pgvector_diagnostics import PostgresPgvectorRepository  # noqa: E402\n",
)
replace_once(
    server,
    "def get_migration_service() -> MigrationService:\n",
    "def get_pgvector_repository() -> PostgresPgvectorRepository:\n"
    '    """Build the bounded read-only pgvector catalog repository."""\n'
    "    return PostgresPgvectorRepository(\n"
    "        get_base_sql_driver(),\n"
    "        timeout_seconds=max(1.0, float(current_query_timeout)),\n"
    "    )\n\n\n"
    "def get_migration_service() -> MigrationService:\n",
)
replace_once(
    server,
    '                "object_inventory": {\n'
    '                    "generic": True,\n'
    '                    "core_catalogs_only": True,\n'
    '                    "max_objects": 500,\n'
    '                    "unknown_object_types": "preserved",\n'
    '                },\n',
    '                "object_inventory": {\n'
    '                    "generic": True,\n'
    '                    "core_catalogs_only": True,\n'
    '                    "max_objects": 500,\n'
    '                    "unknown_object_types": "preserved",\n'
    '                },\n'
    '                "pgvector_diagnostics": {\n'
    '                    "read_only": True,\n'
    '                    "core_catalogs_only": True,\n'
    '                    "extension_functions_called": False,\n'
    '                    "max_items": MAX_PGVECTOR_ITEMS,\n'
    '                    "types": ["vector", "halfvec", "sparsevec", "bit"],\n'
    '                    "index_methods": ["hnsw", "ivfflat"],\n'
    '                },\n',
)
marker = '@mcp.tool(description="Search relations, routines, types, collations, and extensions")\n'
tool = '''@mcp.tool(description="Report bounded pgvector columns and indexes through PostgreSQL core catalogs")
async def get_pgvector_diagnostics(
    max_columns: Annotated[
        int,
        Field(description="Maximum pgvector columns", ge=1, le=MAX_PGVECTOR_ITEMS - 1),
    ] = 250,
    max_indexes: Annotated[
        int,
        Field(description="Maximum pgvector indexes", ge=1, le=MAX_PGVECTOR_ITEMS - 1),
    ] = 250,
) -> ResponseType:
    """Return read-only pgvector column, dimension, index, and validity metadata."""
    try:
        snapshot = await get_pgvector_repository().snapshot(
            max_columns=max_columns,
            max_indexes=max_indexes,
        )
        return format_text_response(snapshot.to_payload())
    except PgvectorCatalogError as exc:
        return format_error_response(str(exc))
    except Exception as exc:
        logger.exception("Unexpected pgvector catalog diagnostic error")
        return format_error_response(str(exc))


'''
replace_once(server, marker, tool + marker)
