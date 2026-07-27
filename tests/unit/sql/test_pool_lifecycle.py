"""Concurrency contracts for the lazy PostgreSQL pool lifecycle."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from postgres_mcp.sql import DbConnPool


class AsyncContext:
    def __init__(self, value: Any):
        self.value = value

    async def __aenter__(self) -> Any:
        return self.value

    async def __aexit__(self, exc_type: Any, exc: BaseException | None, traceback: Any) -> None:
        return None


class FakeCursor:
    async def execute(self, _query: str) -> None:
        return None


class FakeConnection:
    def cursor(self) -> AsyncContext:
        return AsyncContext(FakeCursor())


class ControlledPool:
    def __init__(
        self,
        *,
        open_started: asyncio.Event,
        release_open: asyncio.Event,
        open_error: BaseException | None = None,
    ) -> None:
        self.open_started = open_started
        self.release_open = release_open
        self.open_error = open_error
        self.open_calls = 0
        self.close_calls = 0

    async def open(self) -> None:
        self.open_calls += 1
        self.open_started.set()
        await self.release_open.wait()
        if self.open_error is not None:
            raise self.open_error

    async def close(self) -> None:
        self.close_calls += 1

    def connection(self) -> AsyncContext:
        return AsyncContext(FakeConnection())


@pytest.mark.asyncio
async def test_concurrent_first_use_initializes_exactly_one_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    open_started = asyncio.Event()
    release_open = asyncio.Event()
    created: list[ControlledPool] = []

    def pool_factory(**_kwargs: Any) -> ControlledPool:
        pool = ControlledPool(open_started=open_started, release_open=release_open)
        created.append(pool)
        return pool

    monkeypatch.setattr("postgres_mcp.sql.sql_driver.AsyncConnectionPool", pool_factory)
    pool = DbConnPool("postgresql://example.invalid/app", min_size=0, max_size=2)

    first = asyncio.create_task(pool.pool_connect())
    await open_started.wait()
    second = asyncio.create_task(pool.pool_connect())
    await asyncio.sleep(0)
    release_open.set()

    first_result, second_result = await asyncio.gather(first, second)

    assert first_result is second_result
    assert created == [first_result]
    assert first_result.open_calls == 1
    assert first_result.close_calls == 0


@pytest.mark.asyncio
async def test_failed_initialization_releases_lock_for_clean_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    releases = [asyncio.Event(), asyncio.Event()]
    for release in releases:
        release.set()
    created: list[ControlledPool] = []

    def pool_factory(**_kwargs: Any) -> ControlledPool:
        index = len(created)
        pool = ControlledPool(
            open_started=asyncio.Event(),
            release_open=releases[index],
            open_error=RuntimeError("first open failed") if index == 0 else None,
        )
        created.append(pool)
        return pool

    monkeypatch.setattr("postgres_mcp.sql.sql_driver.AsyncConnectionPool", pool_factory)
    pool = DbConnPool("postgresql://example.invalid/app", min_size=0, max_size=1)

    with pytest.raises(ValueError, match="first open failed"):
        await pool.pool_connect()

    recovered = await asyncio.wait_for(pool.pool_connect(), timeout=1)

    assert recovered is created[1]
    assert pool.is_valid is True
    assert created[0].close_calls == 1
