from pathlib import Path


path = Path("README.md")
source = path.read_text()
replacements = (
    (
        "- Inventory objects that belong to an installed extension.\n",
        "- Inventory objects that belong to an installed extension.\n"
        "- Report PostGIS columns and spatial indexes from PostgreSQL core catalogs.\n",
    ),
    (
        "| `get_extension_objects` | List objects that belong to one extension |\n",
        "| `get_extension_objects` | List objects that belong to one extension |\n"
        "| `get_postgis_diagnostics` | Report bounded PostGIS columns and spatial indexes |\n",
    ),
    (
        "- Extension object inventories return at most 500 objects.\n",
        "- Extension object inventories return at most 500 objects.\n"
        "- PostGIS diagnostics return at most 500 combined columns and indexes.\n",
    ),
)
for old, new in replacements:
    if source.count(old) != 1:
        raise RuntimeError(f"expected one README marker: {old!r}")
    source = source.replace(old, new, 1)
path.write_text(source)
