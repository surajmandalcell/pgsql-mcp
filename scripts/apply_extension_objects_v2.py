from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    source = target.read_text()
    if source.count(old) != 1:
        raise RuntimeError(f"expected one marker in {path}: {old[:100]!r}")
    target.write_text(source.replace(old, new, 1))


server = "src/postgres_mcp/server.py"
replace_once(
    server,
    "from .extension_profiles import PostgresExtensionProfileRepository  # noqa: E402\n",
    "from .extension_profiles import PostgresExtensionProfileRepository  # noqa: E402\n"
    "from .extension_objects import ExtensionObjectError  # noqa: E402\n"
    "from .extension_objects import MAX_EXTENSION_OBJECTS  # noqa: E402\n"
    "from .extension_objects import PostgresExtensionObjectRepository  # noqa: E402\n",
)
replace_once(
    server,
    "def get_migration_service() -> MigrationService:\n",
    "def get_extension_object_repository() -> PostgresExtensionObjectRepository:\n"
    '    """Build the bounded core-catalog extension object repository."""\n'
    "    return PostgresExtensionObjectRepository(\n"
    "        get_base_sql_driver(),\n"
    "        timeout_seconds=max(1.0, float(current_query_timeout)),\n"
    "    )\n\n\n"
    "def get_migration_service() -> MigrationService:\n",
)
replace_once(
    server,
    '                "specialized_tools": ["hypopg", "pg_stat_statements"],\n',
    '                "specialized_tools": ["hypopg", "pg_stat_statements"],\n'
    '                "object_inventory": {\n'
    '                    "generic": True,\n'
    '                    "core_catalogs_only": True,\n'
    '                    "max_objects": 500,\n'
    '                    "unknown_object_types": "preserved",\n'
    "                },\n",
)
marker = '@mcp.tool(description="Search relations, routines, types, collations, and extensions")\n'
tool = '''@mcp.tool(description="Inventory PostgreSQL objects owned by one installed extension through core catalogs")
async def get_extension_objects(
    extension_name: Annotated[str, Field(description="Exact installed extension name")],
    limit: Annotated[
        int,
        Field(description="Maximum extension-owned objects", ge=1, le=MAX_EXTENSION_OBJECTS),
    ] = 100,
) -> ResponseType:
    """Return a bounded deterministic extension-membership inventory."""
    try:
        snapshot = await get_extension_object_repository().snapshot(extension_name, limit=limit)
        return format_text_response(snapshot.to_payload())
    except ExtensionObjectError as exc:
        return format_error_response(str(exc))
    except Exception as exc:
        logger.exception("Unexpected extension object inventory error")
        return format_error_response(str(exc))


'''
replace_once(server, marker, tool + marker)

capability_test = "tests/unit/server/test_extension_profile_tools.py"
replace_once(
    capability_test,
    '        "specialized_tools": ["hypopg", "pg_stat_statements"],\n    }\n',
    '        "specialized_tools": ["hypopg", "pg_stat_statements"],\n'
    '        "object_inventory": {\n'
    '            "generic": True,\n'
    '            "core_catalogs_only": True,\n'
    '            "max_objects": 500,\n'
    '            "unknown_object_types": "preserved",\n'
    "        },\n"
    "    }\n",
)

readme = "README.md"
extension_feature = (
    "- **Extension profiles** — inventory known and unknown installed extensions with honest catalog, type, and specialized-tool support tiers.\n"
)
object_feature = (
    "- **Extension objects** — inventory every registered object owned by any installed extension through PostgreSQL core dependency catalogs.\n"
)
replace_once(readme, extension_feature, extension_feature + object_feature)
replace_once(
    readme,
    "| `get_extension_profiles` | List bounded installed or available extension capability profiles |\n",
    "| `get_extension_profiles` | List bounded installed or available extension capability profiles |\n"
    "| `get_extension_objects` | Inventory bounded extension-owned objects through core PostgreSQL catalogs |\n",
)
replace_once(
    readme,
    "extension profiles in [docs/extensions.md](docs/extensions.md), provider profiles in "
    "[docs/providers.md](docs/providers.md), and the version support contract",
    "extension profiles in [docs/extensions.md](docs/extensions.md), extension-owned objects in "
    "[docs/extension-objects.md](docs/extension-objects.md), provider profiles in "
    "[docs/providers.md](docs/providers.md), and the version support contract",
)
