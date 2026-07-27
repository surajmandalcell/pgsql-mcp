"""Real-PostgreSQL contracts for PostGIS catalog diagnostics."""

from __future__ import annotations

import os

import pytest

from postgres_mcp.postgis_diagnostics import PostgresPostgisRepository
from postgres_mcp.sql import SqlDriver


@pytest.mark.asyncio
async def test_postgis_columns_and_gist_index_use_only_catalog_metadata() -> None:
    connection_string = os.environ.get("PGSQL_MCP_POSTGIS_TEST_URI")
    if not connection_string:
        pytest.skip("set PGSQL_MCP_POSTGIS_TEST_URI in the dedicated PostGIS workflow")

    driver = SqlDriver(engine_url=connection_string)
    try:
        async with driver.connection() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute("DROP SCHEMA IF EXISTS postgis_contract CASCADE")
                await cursor.execute("CREATE SCHEMA postgis_contract")
                await cursor.execute(
                    """
                    CREATE TABLE postgis_contract.places (
                        id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                        geom geometry(PointZ,4326) NOT NULL,
                        area geography(Polygon,4326)
                    )
                    """
                )
                await cursor.execute(
                    """
                    CREATE INDEX places_geom_gist_idx
                    ON postgis_contract.places
                    USING gist (geom)
                    """
                )
            await connection.commit()

        repository = PostgresPostgisRepository(driver, timeout_seconds=10)
        first = await repository.snapshot(max_columns=100, max_indexes=100)
        second = await repository.snapshot(max_columns=100, max_indexes=100)

        assert first == second
        assert first.identity.oid > 0
        assert first.identity.installed_version
        assert first.truncated is False

        columns = {
            (column.schema, column.relation, column.column): column
            for column in first.columns
            if column.schema == "postgis_contract"
        }
        geom = columns[("postgis_contract", "places", "geom")]
        assert geom.type_name == "geometry"
        assert geom.shape == "POINT"
        assert geom.srid == 4326
        assert geom.dimensions == 3
        assert geom.nullable is False

        area = columns[("postgis_contract", "places", "area")]
        assert area.type_name == "geography"
        assert area.shape == "POLYGON"
        assert area.srid == 4326
        assert area.dimensions == 2

        index = next(item for item in first.indexes if item.name == "places_geom_gist_idx")
        assert index.schema == "postgis_contract"
        assert index.relation == "places"
        assert index.access_method == "gist"
        assert index.operator_classes == ("gist_geometry_ops_2d",)
        assert index.valid is True
        assert index.ready is True
        assert first.findings == ()

        payload_text = repr(first.to_payload()).lower()
        assert "password" not in payload_text
        assert "conninfo" not in payload_text
        assert connection_string.lower() not in payload_text
    finally:
        try:
            async with driver.connection() as connection:
                async with connection.cursor() as cursor:
                    await cursor.execute("DROP SCHEMA IF EXISTS postgis_contract CASCADE")
                await connection.commit()
        finally:
            pool = driver.connect()
            if hasattr(pool, "close"):
                await pool.close()
