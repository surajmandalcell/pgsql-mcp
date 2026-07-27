"""Test-first contracts for read-only PostGIS catalog diagnostics."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from postgres_mcp.postgis_diagnostics import MAX_POSTGIS_ITEMS
from postgres_mcp.postgis_diagnostics import PostgisCatalogError
from postgres_mcp.postgis_diagnostics import PostgresPostgisRepository
from postgres_mcp.postgis_diagnostics import parse_spatial_typmod
from postgres_mcp.sql import BoundedQueryResult


def result(rows: list[dict[str, object]], *, truncated: bool = False) -> BoundedQueryResult:
    return BoundedQueryResult(rows, [], len(rows), truncated, None, "SELECT")


def identity_row() -> dict[str, object]:
    return {"extension_oid": 7001, "installed_version": "3.6.4", "schema_name": "public"}


def column_row() -> dict[str, object]:
    return {
        "schema_name": "app",
        "relation_name": "places",
        "column_name": "geom",
        "type_name": "geometry",
        "formatted_type": "geometry(PointZ,4326)",
        "nullable": False,
    }


def index_row() -> dict[str, object]:
    return {
        "schema_name": "app",
        "relation_name": "places",
        "index_name": "places_geom_gist_idx",
        "access_method": "gist",
        "is_unique": False,
        "is_valid": True,
        "is_ready": True,
        "predicate": None,
        "expression": None,
        "definition": "CREATE INDEX places_geom_gist_idx ON app.places USING gist (geom)",
        "operator_classes": ["gist_geometry_ops_2d"],
        "reloptions": None,
    }


@pytest.mark.parametrize(
    ("formatted_type", "base_type", "shape", "srid", "dimensions"),
    [
        ("geometry(Point,4326)", "geometry", "POINT", 4326, 2),
        ("public.geometry(PointZ,4326)", "geometry", "POINT", 4326, 3),
        ("geography(LineStringM,4326)", "geography", "LINESTRING", 4326, 3),
        ("geography(PolygonZM,4326)", "geography", "POLYGON", 4326, 4),
        ("geometry", "geometry", None, None, None),
        ("raster", "raster", None, None, None),
    ],
)
def test_parse_spatial_typmod(
    formatted_type: str,
    base_type: str,
    shape: str | None,
    srid: int | None,
    dimensions: int | None,
) -> None:
    parsed = parse_spatial_typmod(formatted_type)
    assert parsed.base_type == base_type
    assert parsed.shape == shape
    assert parsed.srid == srid
    assert parsed.dimensions == dimensions


def test_parse_spatial_typmod_rejects_malformed_text() -> None:
    with pytest.raises(PostgisCatalogError, match="spatial type"):
        parse_spatial_typmod("geometry(Point,not-an-srid)")
    with pytest.raises(PostgisCatalogError, match="supported PostGIS type"):
        parse_spatial_typmod("numeric(10,2)")


@pytest.mark.asyncio
async def test_repository_reads_identity_columns_and_spatial_indexes() -> None:
    driver = AsyncMock()
    driver.execute_bounded_query.side_effect = [
        result([identity_row()]),
        result([column_row()]),
        result([index_row()]),
    ]
    repository = PostgresPostgisRepository(driver, timeout_seconds=5)

    snapshot = await repository.snapshot(max_columns=60, max_indexes=40)

    assert snapshot.identity.installed_version == "3.6.4"
    assert snapshot.columns[0].shape == "POINT"
    assert snapshot.columns[0].srid == 4326
    assert snapshot.columns[0].dimensions == 3
    assert snapshot.indexes[0].operator_classes == ("gist_geometry_ops_2d",)
    assert snapshot.findings == ()

    calls = driver.execute_bounded_query.await_args_list
    assert calls[0].kwargs == {"max_rows": 1, "force_readonly": True, "timeout_seconds": 5}
    assert calls[1].kwargs == {
        "params": [7001],
        "max_rows": 60,
        "force_readonly": True,
        "timeout_seconds": 5,
    }
    assert calls[2].kwargs == {
        "params": [7001],
        "max_rows": 40,
        "force_readonly": True,
        "timeout_seconds": 5,
    }
    sql = " ".join(" ".join(call.args[0].split()).lower() for call in calls)
    assert "pg_catalog.pg_extension" in sql
    assert "pg_catalog.pg_attribute" in sql
    assert "pg_catalog.pg_index" in sql
    assert "st_srid" not in sql
    assert "geometrytype" not in sql


@pytest.mark.asyncio
async def test_repository_reports_missing_or_ambiguous_postgis_identity() -> None:
    driver = AsyncMock()
    repository = PostgresPostgisRepository(driver)

    driver.execute_bounded_query.return_value = result([])
    with pytest.raises(PostgisCatalogError, match="not installed"):
        await repository.snapshot()

    driver.execute_bounded_query.return_value = result([identity_row(), identity_row()])
    with pytest.raises(PostgisCatalogError, match="exactly one row"):
        await repository.snapshot()


@pytest.mark.asyncio
async def test_repository_rejects_malformed_and_duplicate_rows() -> None:
    driver = AsyncMock()
    malformed = column_row()
    malformed["nullable"] = 1
    driver.execute_bounded_query.side_effect = [result([identity_row()]), result([malformed]), result([])]
    with pytest.raises(PostgisCatalogError, match="nullable"):
        await PostgresPostgisRepository(driver).snapshot()

    driver.execute_bounded_query.side_effect = [
        result([identity_row()]),
        result([column_row(), column_row()]),
        result([]),
    ]
    with pytest.raises(PostgisCatalogError, match="duplicate PostGIS column"):
        await PostgresPostgisRepository(driver).snapshot()

    driver.execute_bounded_query.side_effect = [
        result([identity_row()]),
        result([]),
        result([index_row(), index_row()]),
    ]
    with pytest.raises(PostgisCatalogError, match="duplicate PostGIS index"):
        await PostgresPostgisRepository(driver).snapshot()


def test_repository_limits_and_timeout_are_bounded() -> None:
    driver = AsyncMock()
    with pytest.raises(ValueError, match="positive"):
        PostgresPostgisRepository(driver, timeout_seconds=0)

    repository = PostgresPostgisRepository(driver)
    for limits in ((0, 1), (1, 0), (MAX_POSTGIS_ITEMS, 1), (1, MAX_POSTGIS_ITEMS)):
        with pytest.raises(PostgisCatalogError, match="combined item limit"):
            repository._validate_limits(*limits)  # pyright: ignore[reportPrivateUsage]


def test_snapshot_findings_are_catalog_only() -> None:
    repository = PostgresPostgisRepository(AsyncMock())
    snapshot = repository._snapshot_from_rows(  # pyright: ignore[reportPrivateUsage]
        identity_row(),
        [column_row()],
        [
            {
                **index_row(),
                "is_valid": False,
                "is_ready": False,
                "predicate": "tenant_id IS NOT NULL",
                "operator_classes": ["future_spatial_ops"],
            }
        ],
        truncated=True,
    )

    assert snapshot.truncated is True
    assert [item.to_payload() for item in snapshot.findings] == [
        {"code": "postgis_index_not_valid", "severity": "error", "object": "app.places_geom_gist_idx"},
        {"code": "postgis_index_not_ready", "severity": "warning", "object": "app.places_geom_gist_idx"},
        {"code": "postgis_partial_index", "severity": "info", "object": "app.places_geom_gist_idx"},
        {"code": "postgis_unknown_operator_class", "severity": "info", "object": "app.places_geom_gist_idx"},
    ]
    assert "distance" not in str(snapshot.to_payload()).lower()
