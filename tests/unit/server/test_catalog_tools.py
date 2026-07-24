"""Server-contract tests for advanced catalog and PostgreSQL type tools."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock
from unittest.mock import patch

import pytest
from mcp.types import TextContent

import postgres_mcp.server as server


def payload(response: server.ResponseType) -> object:
    content = response[0]
    assert isinstance(content, TextContent)
    return json.loads(content.text)


@pytest.mark.asyncio
async def test_server_info_uses_base_readonly_catalog_driver() -> None:
    base_driver = AsyncMock()
    with (
        patch.object(server, "get_base_sql_driver", return_value=base_driver),
        patch.object(server, "get_server_info_data", new=AsyncMock(return_value={"database": "app"})) as helper,
    ):
        response = await server.get_server_info()

    assert payload(response) == {"database": "app"}
    helper.assert_awaited_once_with(base_driver)


@pytest.mark.asyncio
async def test_search_catalog_forwards_all_filters() -> None:
    base_driver = AsyncMock()
    with (
        patch.object(server, "get_base_sql_driver", return_value=base_driver),
        patch.object(server, "search_catalog_data", new=AsyncMock(return_value=[{"object_name": "orders"}])) as helper,
    ):
        response = await server.search_catalog(
            "orders",
            schema_name="app",
            object_kind="table",
            include_system=True,
            limit=10,
            offset=2,
        )

    assert payload(response) == [{"object_name": "orders"}]
    helper.assert_awaited_once_with(
        base_driver,
        term="orders",
        schema_name="app",
        object_kind="table",
        include_system=True,
        limit=10,
        offset=2,
    )


@pytest.mark.asyncio
async def test_relation_and_type_tools_forward_structured_identity() -> None:
    base_driver = AsyncMock()
    with (
        patch.object(server, "get_base_sql_driver", return_value=base_driver),
        patch.object(server, "list_relations_data", new=AsyncMock(return_value=[{"relation_kind": "table"}])) as list_relations,
        patch.object(server, "get_relation_details_data", new=AsyncMock(return_value={"oid": 42})) as relation_details,
        patch.object(server, "list_postgres_types_data", new=AsyncMock(return_value=[{"type_kind": "enum"}])) as list_types,
        patch.object(server, "get_postgres_type_data", new=AsyncMock(return_value={"oid": 9001})) as type_details,
    ):
        relations_response = await server.list_relations("app", "table", False, 20, 1)
        relation_response = await server.get_relation_details("app", "items")
        types_response = await server.list_postgres_types("app", "enum", False, 30, 3)
        type_response = await server.get_postgres_type(type_oid=9001)

    assert payload(relations_response) == [{"relation_kind": "table"}]
    assert payload(relation_response) == {"oid": 42}
    assert payload(types_response) == [{"type_kind": "enum"}]
    assert payload(type_response) == {"oid": 9001}
    list_relations.assert_awaited_once_with(
        base_driver,
        schema_name="app",
        relation_kind="table",
        include_system=False,
        limit=20,
        offset=1,
    )
    relation_details.assert_awaited_once_with(base_driver, schema_name="app", relation_name="items")
    list_types.assert_awaited_once_with(
        base_driver,
        schema_name="app",
        type_kind="enum",
        include_system=False,
        limit=30,
        offset=3,
    )
    type_details.assert_awaited_once_with(
        base_driver,
        type_oid=9001,
        schema_name=None,
        type_name=None,
    )


@pytest.mark.asyncio
async def test_catalog_tool_errors_are_stable() -> None:
    with patch.object(server, "search_catalog_data", new=AsyncMock(side_effect=ValueError("bad filter"))):
        response = await server.search_catalog("x")

    content = response[0]
    assert isinstance(content, TextContent)
    assert content.text == "Error: bad filter"


@pytest.mark.asyncio
async def test_capabilities_advertise_dynamic_oid_catalog() -> None:
    capabilities = payload(await server.get_server_capabilities())
    assert isinstance(capabilities, dict)
    assert capabilities["catalog"]["oid_backed"] is True
    assert capabilities["postgres_types"]["dynamic"] is True
    assert capabilities["postgres_types"]["supported_kinds"] == [
        "array",
        "base",
        "composite",
        "domain",
        "enum",
        "multirange",
        "pseudo",
        "range",
    ]
