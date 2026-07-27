"""MCP boundary contracts for PostGIS catalog diagnostics."""

from __future__ import annotations

import json
import subprocess
import sys
from unittest.mock import AsyncMock
from unittest.mock import patch

import pytest
from mcp.types import TextContent

import postgres_mcp.server as server
from postgres_mcp.postgis_diagnostics import PostgisCatalogError
from postgres_mcp.postgis_diagnostics import PostgisColumn
from postgres_mcp.postgis_diagnostics import PostgisIdentity
from postgres_mcp.postgis_diagnostics import PostgisIndex
from postgres_mcp.postgis_diagnostics import PostgisSnapshot


def response_text(response: server.ResponseType) -> str:
    content = response[0]
    assert isinstance(content, TextContent)
    return content.text


def response_payload(response: server.ResponseType) -> object:
    return json.loads(response_text(response))


def snapshot() -> PostgisSnapshot:
    return PostgisSnapshot(
        identity=PostgisIdentity(7001, "3.6.3", "public"),
        columns=(
            PostgisColumn(
                "app",
                "places",
                "geom",
                "geometry",
                "geometry(Point,4326)",
                "POINT",
                4326,
                2,
                False,
            ),
        ),
        indexes=(
            PostgisIndex(
                "app",
                "places",
                "places_geom_gist_idx",
                "gist",
                False,
                True,
                True,
                None,
                None,
                "CREATE INDEX places_geom_gist_idx ON app.places USING gist (geom)",
                ("gist_geometry_ops_2d",),
                {},
            ),
        ),
        findings=(),
        truncated=False,
    )


@pytest.mark.asyncio
async def test_postgis_tool_uses_bounded_readonly_repository() -> None:
    repository = AsyncMock()
    repository.snapshot.return_value = snapshot()

    with patch.object(server, "get_postgis_repository", return_value=repository):
        response = await server.get_postgis_diagnostics(max_columns=30, max_indexes=20)

    payload = response_payload(response)
    assert isinstance(payload, dict)
    assert payload["columns"][0]["shape"] == "POINT"
    assert payload["columns"][0]["srid"] == 4326
    assert payload["indexes"][0]["access_method"] == "gist"
    repository.snapshot.assert_awaited_once_with(max_columns=30, max_indexes=20)


@pytest.mark.asyncio
async def test_postgis_tool_reports_domain_and_unexpected_errors() -> None:
    repository = AsyncMock()
    repository.snapshot.side_effect = PostgisCatalogError("PostGIS extension is not installed")
    with patch.object(server, "get_postgis_repository", return_value=repository):
        response = await server.get_postgis_diagnostics()
    assert response_text(response) == "Error: PostGIS extension is not installed"

    repository.snapshot.side_effect = RuntimeError("catalog unavailable")
    with patch.object(server, "get_postgis_repository", return_value=repository):
        response = await server.get_postgis_diagnostics()
    assert response_text(response) == "Error: catalog unavailable"


def test_postgis_repository_factory_uses_current_driver_and_timeout() -> None:
    driver = object()
    with patch.object(server, "get_base_sql_driver", return_value=driver):
        with patch.object(server, "current_query_timeout", 8):
            repository = server.get_postgis_repository()

    assert repository.sql_driver is driver
    assert repository.timeout_seconds == 8


@pytest.mark.asyncio
async def test_capabilities_advertise_readonly_postgis_catalog_diagnostics() -> None:
    payload = response_payload(await server.get_server_capabilities())
    assert isinstance(payload, dict)
    assert payload["extensions"]["postgis_diagnostics"] == {
        "read_only": True,
        "core_catalogs_only": True,
        "extension_functions_called": False,
        "max_items": 500,
        "types": ["geometry", "geography", "raster"],
        "index_methods": ["gist", "spgist", "brin"],
    }


def test_lite_and_ha_profiles_do_not_import_postgis_diagnostic_domain() -> None:
    for module in ("postgres_mcp.lite_server", "postgres_mcp.ha_server"):
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                f"import sys; import {module}; assert 'postgres_mcp.postgis_diagnostics' not in sys.modules",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, completed.stderr
