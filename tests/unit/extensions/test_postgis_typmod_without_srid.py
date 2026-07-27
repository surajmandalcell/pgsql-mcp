"""PostGIS 3.6 typmod contract for a shape without an explicit SRID."""

from postgres_mcp.postgis_diagnostics import parse_spatial_typmod


def test_geometry_shape_without_srid_is_preserved() -> None:
    parsed = parse_spatial_typmod("geometry(MultiPolygon)")

    assert parsed.base_type == "geometry"
    assert parsed.shape == "MULTIPOLYGON"
    assert parsed.srid is None
    assert parsed.dimensions == 2
