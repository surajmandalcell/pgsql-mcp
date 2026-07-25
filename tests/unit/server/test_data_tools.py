"""MCP adapter contracts for structured data operations."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock
from unittest.mock import patch

import pytest
from mcp.types import TextContent

import postgres_mcp.server as server
from postgres_mcp.data_ops import MutationResult
from postgres_mcp.data_ops import RowPage
from postgres_mcp.runtime import AccessMode


def payload(response: server.ResponseType) -> object:
    content = response[0]
    assert isinstance(content, TextContent)
    try:
        return json.loads(content.text)
    except json.JSONDecodeError:
        return content.text


@pytest.mark.asyncio
async def test_select_rows_is_available_in_restricted_mode_and_builds_typed_request() -> None:
    service = AsyncMock()
    service.select.return_value = RowPage(rows=({"id": 1},), next_cursor="next", truncated=True)
    with (
        patch.object(server, "get_data_service", return_value=service),
        patch.object(server, "current_access_mode", AccessMode.RESTRICTED),
    ):
        response = await server.select_rows(
            schema_name="app",
            relation_name="items",
            columns=["id"],
            where=server.FilterSetInput(all=[server.FilterConditionInput(column="tenant_id", operator="eq", value=7)]),
            order_by=[server.OrderTermInput(column="id", direction="asc")],
            limit=10,
        )

    assert payload(response) == {"rows": [{"id": 1}], "next_cursor": "next", "truncated": True, "truncation_reason": None}
    request = service.select.await_args.args[0]
    assert request.relation.display_name == "app.items"
    assert request.filters.all_of[0].column == "tenant_id"


@pytest.mark.asyncio
async def test_mutations_require_unrestricted_mode_before_service_access() -> None:
    service = AsyncMock()
    with (
        patch.object(server, "get_data_service", return_value=service),
        patch.object(server, "current_access_mode", AccessMode.RESTRICTED),
    ):
        response = await server.insert_rows(
            schema_name="app",
            relation_name="items",
            rows=[{"name": "one"}],
            returning=["id"],
            max_affected_rows=1,
        )

    assert payload(response) == "Error: insert_rows requires unrestricted mode"
    service.insert.assert_not_awaited()


@pytest.mark.asyncio
async def test_insert_update_delete_and_upsert_forward_guards() -> None:
    service = AsyncMock()
    service.insert.return_value = MutationResult(1, ({"id": 1},))
    service.upsert.return_value = MutationResult(1, ({"id": 1},))
    service.update.return_value = MutationResult(1, ({"id": 1},))
    service.delete.return_value = MutationResult(1, ({"id": 1},))
    where = server.FilterSetInput(all=[server.FilterConditionInput(column="id", operator="eq", value=1)])

    with (
        patch.object(server, "get_data_service", return_value=service),
        patch.object(server, "current_access_mode", AccessMode.UNRESTRICTED),
    ):
        assert payload(
            await server.insert_rows("app", "items", [{"name": "one"}], ["id"], 1, 1)
        ) == {"affected_rows": 1, "rows": [{"id": 1}]}
        assert payload(
            await server.upsert_rows("app", "items", [{"email": "one@example.com"}], ["email"], [], ["id"], 1, 1)
        ) == {"affected_rows": 1, "rows": [{"id": 1}]}
        assert payload(
            await server.update_rows("app", "items", {"name": "updated"}, where, None, ["id"], 1, 1)
        ) == {"affected_rows": 1, "rows": [{"id": 1}]}
        assert payload(
            await server.delete_rows("app", "items", where, None, ["id"], 1, 1)
        ) == {"affected_rows": 1, "rows": [{"id": 1}]}

    assert service.insert.await_args.args[0].guard.expected_rows == 1
    assert service.upsert.await_args.args[0].conflict_columns == ("email",)
    assert service.update.await_args.args[0].guard.max_affected_rows == 1
    assert service.delete.await_args.args[0].filters.term_count == 1


@pytest.mark.asyncio
async def test_capabilities_advertise_bounded_structured_data_operations() -> None:
    capabilities = payload(await server.get_server_capabilities())
    assert isinstance(capabilities, dict)
    assert capabilities["data_operations"]["structured_filters"] is True
    assert capabilities["data_operations"]["keyset_pagination"] is True
    assert capabilities["data_operations"]["max_rows"] == 500
    assert capabilities["data_operations"]["max_result_bytes"] == 512 * 1024
