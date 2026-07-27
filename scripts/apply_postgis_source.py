from pathlib import Path


def replace_once_or_accept(path: Path, old: str, new: str) -> None:
    source = path.read_text()
    if source.count(new) == 1 and source.count(old) == 0:
        return
    if source.count(old) != 1 or source.count(new) != 0:
        raise RuntimeError(f"expected one PostGIS source marker: {old!r}")
    path.write_text(source.replace(old, new, 1))


path = Path("src/postgres_mcp/postgis_diagnostics.py")
replace_once_or_accept(
    path,
    "member.objid = ANY(index.indclass)",
    "member.objid = ANY(index.indclass::oid[])",
)
replace_once_or_accept(
    path,
    'r"(geometry|geography|raster)(?:\\(([A-Za-z0-9_]+),(-?[0-9]+)\\))?$"',
    'r"(geometry|geography|raster)(?:\\(([A-Za-z0-9_]+)(?:,(-?[0-9]+))?\\))?$"',
)
replace_once_or_accept(
    path,
    '    if base_type == "raster" or srid_token is None:\n'
    '        raise PostgisCatalogError("spatial type modifier is malformed")\n',
    '    if base_type == "raster":\n'
    '        raise PostgisCatalogError("spatial type modifier is malformed")\n',
)
replace_once_or_accept(
    path,
    "    srid = int(srid_token)\n"
    "    if srid < -1:\n"
    '        raise PostgisCatalogError("spatial type SRID is invalid")\n'
    "    return SpatialTypmod(base_type, upper, srid, dimensions)\n",
    "    srid = int(srid_token) if srid_token is not None else None\n"
    "    if srid is not None and srid < -1:\n"
    '        raise PostgisCatalogError("spatial type SRID is invalid")\n'
    "    return SpatialTypmod(base_type, upper, srid, dimensions)\n",
)
