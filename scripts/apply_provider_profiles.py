from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    source = target.read_text()
    if source.count(old) != 1:
        raise RuntimeError(f"expected one marker in {path}: {old[:100]!r}")
    target.write_text(source.replace(old, new, 1))


provider_module = "src/postgres_mcp/provider_profiles.py"
replace_once(
    provider_module,
    '''        _MANAGED_NOTES
        + ("This profile describes Supabase-hosted deployments; self-hosted Supabase should use upstream or explicit generic-managed policy.",),
''',
    '''        (
            *_MANAGED_NOTES,
            "This profile describes Supabase-hosted deployments; self-hosted Supabase should use upstream or explicit generic-managed policy.",
        ),
''',
)

server = "src/postgres_mcp/server.py"
replace_once(
    server,
    "from .maintenance import ReconciliationResolution  # noqa: E402\n",
    "from .maintenance import ReconciliationResolution  # noqa: E402\n"
    "from .provider_profiles import PostgresProviderProfileRepository  # noqa: E402\n"
    "from .provider_profiles import ProviderProfileError  # noqa: E402\n",
)
replace_once(
    server,
    "def get_migration_service() -> MigrationService:\n",
    "def get_provider_profile_repository() -> PostgresProviderProfileRepository:\n"
    '    """Build the conservative read-only deployment profile repository."""\n'
    "    return PostgresProviderProfileRepository(\n"
    "        get_base_sql_driver(),\n"
    "        timeout_seconds=max(1.0, float(current_query_timeout)),\n"
    "    )\n\n\n"
    "def get_migration_service() -> MigrationService:\n",
)
replace_once(
    server,
    '            "migrations": {\n',
    '            "deployment_profiles": {\n'
    '                "automatic_detection": "strong_markers_only",\n'
    '                "unknown_without_marker": True,\n'
    '                "explicit_hint_supported": True,\n'
    '                "secrets_read": False,\n'
    '                "supported_profiles": [\n'
    '                    "upstream",\n'
    '                    "generic_managed",\n'
    '                    "aws_rds",\n'
    '                    "aws_aurora",\n'
    '                    "google_cloud_sql",\n'
    '                    "google_alloydb",\n'
    '                    "azure_flexible_server",\n'
    '                    "neon",\n'
    '                    "supabase_hosted",\n'
    '                ],\n'
    '            },\n'
    '            "migrations": {\n',
)
marker = '@mcp.tool(description="Search relations, routines, types, collations, and extensions")\n'
tool = '''@mcp.tool(description="Report conservative PostgreSQL deployment-provider capabilities without secrets")
async def get_deployment_profile(
    provider_hint: Annotated[
        str,
        Field(
            description=(
                "auto, upstream, generic_managed, aws_rds, aws_aurora, google_cloud_sql, "
                "google_alloydb, azure_flexible_server, neon, or supabase_hosted"
            )
        ),
    ] = "auto",
) -> ResponseType:
    """Return explicit or strong-marker provider identity plus standard runtime capabilities."""
    try:
        snapshot = await get_provider_profile_repository().snapshot(provider_hint=provider_hint)
        return format_text_response(snapshot.to_payload())
    except ProviderProfileError as exc:
        return format_error_response(str(exc))
    except Exception as exc:
        logger.exception("Unexpected provider profile error")
        return format_error_response(str(exc))


'''
replace_once(server, marker, tool + marker)

readme = "README.md"
extension_feature = (
    "- **Extension profiles** — inventory known and unknown installed extensions "
    "with honest catalog, type, and specialized-tool support tiers.\n"
)
provider_feature = (
    "- **Provider profiles** — distinguish explicit and strongly detected managed "
    "PostgreSQL deployments without reading secrets or guessing weak markers.\n"
)
replace_once(readme, extension_feature, extension_feature + provider_feature)
replace_once(
    readme,
    "| `get_extension_profiles` | List bounded installed or available extension capability profiles |\n",
    "| `get_extension_profiles` | List bounded installed or available extension capability profiles |\n"
    "| `get_deployment_profile` | Report explicit or strong-marker provider constraints and standard runtime capabilities |\n",
)
old_docs = "extension profiles in [docs/extensions.md](docs/extensions.md), and the version support contract"
new_docs = (
    "extension profiles in [docs/extensions.md](docs/extensions.md), provider profiles in "
    "[docs/providers.md](docs/providers.md), and the version support contract"
)
replace_once(readme, old_docs, new_docs)

workflow = ".github/workflows/postgres-compatibility.yml"
replace_once(
    workflow,
    "            tests/integration/replication \\\n",
    "            tests/integration/replication \\\n            tests/integration/providers \\\n",
)
