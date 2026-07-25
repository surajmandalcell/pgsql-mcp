"""Application-service contracts for data operations."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from postgres_mcp.data_ops import DataRepository
from postgres_mcp.data_ops import DataService
from postgres_mcp.data_ops import FilterSet
from postgres_mcp.data_ops import InsertRowsRequest
from postgres_mcp.data_ops import MutationGuard
from postgres_mcp.data_ops import MutationResult
from postgres_mcp.data_ops import OrderTerm
from postgres_mcp.data_ops import QualifiedRelation
from postgres_mcp.data_ops import RowPage
from postgres_mcp.data_ops import SelectRowsRequest


@pytest.mark.asyncio
async def test_service_delegates_immutable_requests_to_repository() -> None:
    repository = AsyncMock(spec=DataRepository)
    service = DataService(repository)
    select_request = SelectRowsRequest(
        relation=QualifiedRelation("app", "items"),
        columns=("id",),
        filters=FilterSet(),
        order_by=(OrderTerm("id"),),
        limit=10,
    )
    repository.select.return_value = RowPage(rows=({"id": 1},), next_cursor=None, truncated=False)

    selected = await service.select(select_request)

    assert selected.rows == ({"id": 1},)
    repository.select.assert_awaited_once_with(select_request)

    insert_request = InsertRowsRequest(
        relation=QualifiedRelation("app", "items"),
        rows=({"name": "one"},),
        returning=("id",),
        guard=MutationGuard(max_affected_rows=1),
    )
    repository.insert.return_value = MutationResult(affected_rows=1, rows=({"id": 1},))

    inserted = await service.insert(insert_request)

    assert inserted.affected_rows == 1
    repository.insert.assert_awaited_once_with(insert_request)
