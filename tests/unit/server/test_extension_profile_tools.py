"""MCP boundary contracts for generic extension capability profiles."""

from __future__ import annotations

import json
import subprocess
import sys
from unittest.mock import AsyncMock
from unittest.mock import patch

import pytest
from mcp.types import TextContent

import postgres_mcp.server as server
from postgres_mcp.extension_profiles import ExtensionFamily
from postgres_mcp.extension_profiles import ExtensionProfile
from postgres_mcp.extension_profiles import ExtensionProfileError
from postgres_mcp.extension_profiles import ExtensionProfilesSnapshot


def response_text(response: server.ResponseType) -> str:
    content = response[0]
    assert isinstance(content, TextContent)
    return content.text


def response_payload(response: server.ResponseType) -> object:
    return json.loads(response_text(response))


def snapshot() -> ExtensionProfilesSnapshot:
    return ExtensionProfilesSnapshot(
        (
            ExtensionProfile(
                name="vector",
                family=ExtensionFamily.PGVECTOR,
                installed=True,
                installed_version="0.8.0",
                default_version="0.8.0",
                schema="extensions",
                comment="vector data type",
                support_tier="catalog_and_type_compatible",
                capabilities=("vector_type", "vector_index_metadata", "unknown_type_preservation"),
                specialized_tools=(),
            ),
        ),
        include_available=False,
        truncated=False,
    )


@pytest.mark.asyncio
async def test_extension_profiles_tool_uses_bounded_readonly_repository() -> None:
    repository = AsyncMock()
    repository.snapshot.return_value = snapshot()

    with patch.object(server, "get_extension_profile_repository", return_value=repository):
        response = await server.get_extension_profiles(include_available=False)

    payload = response_payload(response)
    assert isinstance(payload, dict)
    assert payload["profiles"][0]["name"] == "vector"
    assert payload["profiles"][0]["family"] == "pgvector"
    repository.snapshot.assert_awaited_once_with(include_available=False)


@pytest.mark.asyncio
async def test_extension_profiles_tool_reports_domain_and_unexpected_errors() -> None:
    repository = AsyncMock()
    repository.snapshot.side_effect = ExtensionProfileError("malformed extension catalog")
    with patch.object(server, "get_extension_profile_repository", return_value=repository):
        response = await server.get_extension_profiles()
    assert response_text(response) == "Error: malformed extension catalog"

    repository.snapshot.side_effect = RuntimeError("catalog unavailable")
    with patch.object(server, "get_extension_profile_repository", return_value=repository):
        response = await server.get_extension_profiles()
    assert response_text(response) == "Error: catalog unavailable"


@pytest.mark.asyncio
async def test_capabilities_advertise_extension_profiles_without_claiming_full_extension_tooling() -> None:
    payload = response_payload(await server.get_server_capabilities())
    assert isinstance(payload, dict)
    assert payload["extensions"] == {
        "dynamic_inventory": True,
        "unknown_extensions": "preserved_as_generic_catalog_profiles",
        "known_families": ["postgis", "timescaledb", "citus", "pgvector", "hypopg", "pg_stat_statements"],
        "catalog_and_type_compatible": ["postgis", "timescaledb", "citus", "pgvector"],
        "specialized_tools": ["hypopg", "pg_stat_statements"],
        "object_inventory": {
            "generic": True,
            "core_catalogs_only": True,
            "max_objects": 500,
            "unknown_object_types": "preserved",
        },
        "postgis_diagnostics": {
            "read_only": True,
            "core_catalogs_only": True,
            "extension_functions_called": False,
            "max_items": 500,
            "types": ["geometry", "geography", "raster"],
            "index_methods": ["gist", "spgist", "brin"],
        },
    }


def test_lite_profile_does_not_import_extension_profile_domain() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            ("import sys; import postgres_mcp.lite_server; assert 'postgres_mcp.extension_profiles' not in sys.modules"),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
