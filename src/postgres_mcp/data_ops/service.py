"""Application service for structured data operations."""

from __future__ import annotations

from typing import Protocol

from .domain import DeleteRowsRequest
from .domain import InsertRowsRequest
from .domain import MutationResult
from .domain import RowPage
from .domain import SelectRowsRequest
from .domain import UpdateRowsRequest
from .domain import UpsertRowsRequest


class DataRepository(Protocol):
    """Infrastructure port owned by the data-operations bounded context."""

    async def select(self, request: SelectRowsRequest) -> RowPage: ...

    async def insert(self, request: InsertRowsRequest) -> MutationResult: ...

    async def upsert(self, request: UpsertRowsRequest) -> MutationResult: ...

    async def update(self, request: UpdateRowsRequest) -> MutationResult: ...

    async def delete(self, request: DeleteRowsRequest) -> MutationResult: ...


class DataService:
    """Use-case boundary that accepts validated immutable requests."""

    def __init__(self, repository: DataRepository):
        self._repository = repository

    async def select(self, request: SelectRowsRequest) -> RowPage:
        return await self._repository.select(request)

    async def insert(self, request: InsertRowsRequest) -> MutationResult:
        return await self._repository.insert(request)

    async def upsert(self, request: UpsertRowsRequest) -> MutationResult:
        return await self._repository.upsert(request)

    async def update(self, request: UpdateRowsRequest) -> MutationResult:
        return await self._repository.update(request)

    async def delete(self, request: DeleteRowsRequest) -> MutationResult:
        return await self._repository.delete(request)
