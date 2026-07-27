"""MCP boundary contracts for generic extension-owned object inventory."""

from __future__ import annotations

import json
import subprocess
import sys
from unittest.mock import AsyncMock
from unittest.mock import patch

import pytest
from mcp.types import TextContent

import postgres_mcp.server as server
from postgres_mcp.extension_objects import ExtensionIdentity
from postgres_mcp.extension_objects import ExtensionObjectError
from postgres_mcp.extension_objects import ExtensionObjectSnapshot
from postgres_mcp.extension_objects import ExtensionOwnedObject


def response_text(response: server.ResponseType) -> str:
    content = response[0]
    assert isinstance(content, TextContent)
    return content.text


def response_payload(response: server.ResponseType) -> object:
    return json.loads(response_text(response))


def snapshot() -> ExtensionObjectSnapshot:
    return ExtensionObjectSnapshot(
        extension=ExtensionIdentity(
            oid=123,
            name="vector",
            installed_version="0.8.0",
            schema="extensions",
            relocatable=True,
        ),
        objects=(
            ExtensionOwnedObject(
                object_type="type",
                schema="extensions",
                name="vector",
                identity="extensions.vector",
                catalog="pg_type",
                object_oid=456,
                object_sub_id=0,
            ),
        ),
        truncated=False,
    )


@pytest.mark.asyncio
async def test_extension_object_tool_uses_bounded_readonly_repository() -> None:
    repository = AsyncMock()
    repository.snapshot.return_value = snapshot()

    with patch.object(server, "get_extension_object_repository", return_value=repository):
        response = await server.get_extension_objects("vector", limit=25)

    payload = response_payload(response)
    assert isinstance(payload, dict)
    assert payload["extension"]["name"] == "vector"
    assert payload["object_types"] == {"type": 1}
    repository.snapshot.assert_awaited_once_with("vector", limit=25)


@pytest.mark.asyncio
async def test_extension_object_tool_reports_domain_and_unexpected_errors() -> None:
    repository = AsyncMock()
    repository.snapshot.side_effect = ExtensionObjectError("extension is not installed")
    with patch.object(server, "get_extension_object_repository", return_value=repository):
        response = await server.get_extension_objects("missing")
    assert response_text(response) == "Error: extension is not installed"

    repository.snapshot.side_effect = RuntimeError("catalog unavailable")
    with patch.object(server, "get_extension_object_repository", return_value=repository):
        response = await server.get_extension_objects("vector")
    assert response_text(response) == "Error: catalog unavailable"


@pytest.mark.asyncio
async def test_capabilities_advertise_generic_extension_object_inventory() -> None:
    payload = response_payload(await server.get_server_capabilities())
    assert isinstance(payload, dict)
    assert payload["extensions"]["object_inventory"] == {
        "generic": True,
        "core_catalogs_only": True,
        "max_objects": 500,
        "unknown_object_types": "preserved",
    }


def test_lite_and_ha_profiles_do_not_import_extension_object_domain() -> None:
    for module in ("postgres_mcp.lite_server", "postgres_mcp.ha_server"):
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                f"import sys; import {module}; assert 'postgres_mcp.extension_objects' not in sys.modules",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, completed.stderr
