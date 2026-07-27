from pathlib import Path


path = Path("src/postgres_mcp/postgis_diagnostics.py")
source = path.read_text()
old = "member.objid = ANY(index.indclass)"
new = "member.objid = ANY(index.indclass::oid[])"
if source.count(old) != 1:
    raise RuntimeError("expected one PostGIS operator-class vector comparison")
path.write_text(source.replace(old, new, 1))
