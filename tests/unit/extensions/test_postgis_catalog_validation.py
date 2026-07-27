"""Defensive catalog validation for PostGIS diagnostics."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from postgres_mcp.postgis_diagnostics import PostgisCatalogError
from postgres_mcp.postgis_diagnostics import PostgresPostgisRepository
from postgres_mcp.postgis_diagnostics import parse_spatial_typmod


def identity_row() -> dict[str, object]:
    return {
        "extension_oid": 7001,
        "installed_version": "3.6.4",
        "schema_name": "public",
    }


def column_row() -> dict[str, object]:
    return {
        "schema_name": "app",
        "relation_name": "places",
        "column_name": "geom",
        "type_name": "geometry",
        "formatted_type": "geometry(Point,4326)",
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


def snapshot(
    *,
    identity: dict[str, object] | None = None,
    columns: list[dict[str, object]] | None = None,
    indexes: list[dict[str, object]] | None = None,
) -> None:
    repository = PostgresPostgisRepository(AsyncMock())
    repository._snapshot_from_rows(  # pyright: ignore[reportPrivateUsage]
        identity or identity_row(),
        columns or [],
        indexes or [],
        truncated=False,
    )


@pytest.mark.parametrize("formatted_type", ["", "x" * 257])
def test_spatial_type_text_must_be_bounded(formatted_type: str) -> None:
    with pytest.raises(PostgisCatalogError, match="bounded text"):
        parse_spatial_typmod(formatted_type)


def test_raster_typmod_is_rejected() -> None:
    with pytest.raises(PostgisCatalogError, match="modifier is malformed"):
        parse_spatial_typmod("raster(Tile,4326)")


def test_empty_shape_suffix_is_rejected() -> None:
    with pytest.raises(PostgisCatalogError, match="shape is malformed"):
        parse_spatial_typmod("geometry(Z,4326)")


def test_invalid_negative_srid_is_rejected() -> None:
    with pytest.raises(PostgisCatalogError, match="SRID is invalid"):
        parse_spatial_typmod("geometry(Point,-2)")


def test_identity_oid_must_be_positive() -> None:
    malformed = identity_row()
    malformed["extension_oid"] = 0

    with pytest.raises(PostgisCatalogError, match="extension_oid"):
        snapshot(identity=malformed)


def test_required_catalog_text_must_be_nonempty() -> None:
    malformed = identity_row()
    malformed["installed_version"] = ""

    with pytest.raises(PostgisCatalogError, match="installed_version"):
        snapshot(identity=malformed)


def test_unsupported_column_type_is_rejected() -> None:
    malformed = column_row()
    malformed["type_name"] = "future_geometry"

    with pytest.raises(PostgisCatalogError, match="not supported"):
        snapshot(columns=[malformed])


def test_optional_catalog_text_must_be_text() -> None:
    malformed = index_row()
    malformed["predicate"] = 1

    with pytest.raises(PostgisCatalogError, match="predicate"):
        snapshot(indexes=[malformed])


def test_operator_classes_must_be_a_text_array() -> None:
    malformed = index_row()
    malformed["operator_classes"] = "gist_geometry_ops_2d"

    with pytest.raises(PostgisCatalogError, match="text array"):
        snapshot(indexes=[malformed])


@pytest.mark.parametrize(
    "reloptions",
    [
        ["fillfactor"],
        ["pages_per_range=64", "pages_per_range=128"],
    ],
)
def test_index_options_must_be_unique_key_value_entries(reloptions: list[str]) -> None:
    malformed = index_row()
    malformed["reloptions"] = reloptions

    with pytest.raises(PostgisCatalogError, match="unique key=value"):
        snapshot(indexes=[malformed])
