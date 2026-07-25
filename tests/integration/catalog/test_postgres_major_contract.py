"""Verify each compatibility job is backed by the requested PostgreSQL major."""

from __future__ import annotations

import pytest

from postgres_mcp.catalog_advanced import get_server_info_data
from postgres_mcp.sql import DbConnPool
from postgres_mcp.sql import SqlDriver


@pytest.mark.asyncio
async def test_selected_postgres_image_matches_the_live_server_major(
    test_postgres_connection_string: tuple[str, str],
) -> None:
    connection_string, image = test_postgres_connection_string
    expected_major = int(image.rsplit(":", maxsplit=1)[-1])
    driver = SqlDriver(engine_url=connection_string)

    try:
        server = await get_server_info_data(driver)
        assert server["server_version_num"] // 10_000 == expected_major
    finally:
        if isinstance(driver.conn, DbConnPool):
            await driver.conn.close()
