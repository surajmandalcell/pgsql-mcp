"""Boundary contract for a missing lazy-pool connection URL."""

from __future__ import annotations

import pytest

from postgres_mcp.sql import DbConnPool


@pytest.mark.asyncio
async def test_pool_connect_without_url_records_stable_invalid_state() -> None:
    pool = DbConnPool(min_size=0, max_size=1)

    with pytest.raises(ValueError, match="Database connection URL not provided"):
        await pool.pool_connect()

    assert pool.is_valid is False
    assert pool.last_error == "Database connection URL not provided"
    assert pool.pool is None
