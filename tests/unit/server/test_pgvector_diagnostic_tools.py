"""MCP boundary contracts for pgvector catalog diagnostics."""

from __future__ import annotations

import json
import subprocess
import sys
from unittest.mock import AsyncMock
from unittest.mock import patch

import pytest
from mcp.types import TextContent

import postgres_mcp.server as server
from postgres_mcp.pgvector_diagnostics import PgvectorCatalogError
from postgres_mcp.pgvector_diagnostics import PgvectorColumn
from postgres_mcp.pgvector_diagnostics import PgvectorIdentity
from postgres_mcp.pgvector_diagnostics import PgvectorIndex
from postgres_mcp.pgvector_diagnostics import PgvectorSnapshot


def response_text(response: server.ResponseType) -> str:
    content = response[0]
    assert isinstance(content, TextContent)
    return content.text


def response_payload(response: server.ResponseType) -> object:
    return json.loads(response_text(response))


def snapshot() -> PgvectorSnapshot:
    return PgvectorSnapshot(
        identity=PgvectorIdentity(9001, "0.8.2", "extensions"),
        columns=(PgvectorColumn("app", "items", "embedding", "vector", "vector(3)", 3, False),),
        indexes=(
            PgvectorIndex(
                "app",
                "items",
                "items_embedding_hnsw_idx",
                "hnsw",
                False,
                True,
                True,
                None,
                None,
                "CREATE INDEX items_embedding_hnsw_idx ON app.items USING hnsw (embedding vector_cosine_ops)",
                ("vector_cosine_ops",),
                {"m": "16"},
            ),
        ),
        findings=(),
        truncated=False,
    )


@pytest.mark.asyncio
async def test_pgvector_tool_uses_bounded_readonly_repository() -> None:
    repository = AsyncMock()
    repository.snapshot.return_value = snapshot()

    with patch.object(server, "get_pgvector_repository", return_value=repository):
        response = await server.get_pgvector_diagnostics(max_columns=30, max_indexes=20)

    payload = response_payload(response)
    assert isinstance(payload, dict)
    assert payload["extension"]["installed_version"] == "0.8.2"
    assert payload["columns"][0]["dimensions"] == 3
    assert payload["indexes"][0]["access_method"] == "hnsw"
    repository.snapshot.assert_awaited_once_with(max_columns=30, max_indexes=20)


@pytest.mark.asyncio
async def test_pgvector_tool_reports_domain_and_unexpected_errors() -> None:
    repository = AsyncMock()
    repository.snapshot.side_effect = PgvectorCatalogError("pgvector extension is not installed")
    with patch.object(server, "get_pgvector_repository", return_value=repository):
        response = await server.get_pgvector_diagnostics()
    assert response_text(response) == "Error: pgvector extension is not installed"

    repository.snapshot.side_effect = RuntimeError("catalog unavailable")
    with patch.object(server, "get_pgvector_repository", return_value=repository):
        response = await server.get_pgvector_diagnostics()
    assert response_text(response) == "Error: catalog unavailable"


def test_pgvector_repository_factory_uses_current_driver_and_timeout() -> None:
    driver = object()
    with patch.object(server, "get_base_sql_driver", return_value=driver):
        with patch.object(server, "current_query_timeout", 7):
            repository = server.get_pgvector_repository()

    assert repository.sql_driver is driver
    assert repository.timeout_seconds == 7


@pytest.mark.asyncio
async def test_capabilities_advertise_readonly_pgvector_catalog_diagnostics() -> None:
    payload = response_payload(await server.get_server_capabilities())
    assert isinstance(payload, dict)
    assert payload["extensions"]["pgvector_diagnostics"] == {
        "read_only": True,
        "core_catalogs_only": True,
        "extension_functions_called": False,
        "max_items": 500,
        "types": ["vector", "halfvec", "sparsevec", "bit"],
        "index_methods": ["hnsw", "ivfflat"],
    }


def test_lite_and_ha_profiles_do_not_import_pgvector_diagnostic_domain() -> None:
    for module in ("postgres_mcp.lite_server", "postgres_mcp.ha_server"):
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                f"import sys; import {module}; assert 'postgres_mcp.pgvector_diagnostics' not in sys.modules",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, completed.stderr
