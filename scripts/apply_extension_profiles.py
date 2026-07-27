from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    content = target.read_text()
    if content.count(old) != 1:
        raise RuntimeError(f"expected exactly one marker in {path}: {old[:80]!r}")
    target.write_text(content.replace(old, new, 1))


server = "src/postgres_mcp/server.py"
replace_once(
    server,
    "from .explain import ExplainPlanTool  # noqa: E402\n",
    "from .explain import ExplainPlanTool  # noqa: E402\n"
    "from .extension_profiles import ExtensionProfileError  # noqa: E402\n"
    "from .extension_profiles import PostgresExtensionProfileRepository  # noqa: E402\n",
)
replace_once(
    server,
    "def get_migration_service() -> MigrationService:\n",
    "def get_extension_profile_repository() -> PostgresExtensionProfileRepository:\n"
    '    """Build the bounded read-only extension inventory repository."""\n'
    "    return PostgresExtensionProfileRepository(\n"
    "        get_base_sql_driver(),\n"
    "        timeout_seconds=max(1.0, float(current_query_timeout)),\n"
    "    )\n\n\n"
    "def get_migration_service() -> MigrationService:\n",
)
replace_once(
    server,
    '            "migrations": {\n',
    '            "extensions": {\n'
    '                "dynamic_inventory": True,\n'
    '                "unknown_extensions": "preserved_as_generic_catalog_profiles",\n'
    '                "known_families": ["postgis", "timescaledb", "citus", "pgvector", "hypopg", "pg_stat_statements"],\n'
    '                "catalog_and_type_compatible": ["postgis", "timescaledb", "citus", "pgvector"],\n'
    '                "specialized_tools": ["hypopg", "pg_stat_statements"],\n'
    "            },\n"
    '            "migrations": {\n',
)
marker = '@mcp.tool(description="Search relations, routines, types, collations, and extensions")\n'
tool = '''@mcp.tool(description="List installed or available PostgreSQL extension capability profiles")
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


'''
replace_once(server, marker, tool + marker)

readme = "README.md"
replace_once(
    readme,
    "- **Schema intelligence** — inspect schemas, tables, views, sequences, columns, constraints, indexes, comments, and extensions.\n",
    "- **Schema intelligence** — inspect schemas, tables, views, sequences, columns, constraints, indexes, comments, and extensions.\n"
    "- **Extension profiles** — inventory known and unknown installed extensions with honest catalog, type, and specialized-tool support tiers.\n",
)
replace_once(
    readme,
    "| `get_server_info` | Report PostgreSQL version, role, recovery, locale, and extensions |\n",
    "| `get_server_info` | Report PostgreSQL version, role, recovery, locale, and extensions |\n"
    "| `get_extension_profiles` | List bounded installed or available extension capability profiles |\n",
)
old_docs = (
    "reviewed maintenance in [docs/maintenance.md](docs/maintenance.md), "
    "replication and HA diagnostics in [docs/replication.md](docs/replication.md), "
    "and the version support contract"
)
new_docs = (
    "reviewed maintenance in [docs/maintenance.md](docs/maintenance.md), "
    "replication and HA diagnostics in [docs/replication.md](docs/replication.md), "
    "extension profiles in [docs/extensions.md](docs/extensions.md), "
    "and the version support contract"
)
replace_once(readme, old_docs, new_docs)
