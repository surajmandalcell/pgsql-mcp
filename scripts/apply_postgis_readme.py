from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    source = target.read_text()
    if source.count(old) != 1:
        raise RuntimeError(f"expected one marker in {path}: {old!r}")
    target.write_text(source.replace(old, new, 1))


readme = "README.md"
replace_once(
    readme,
    "- Inventory objects that belong to an installed extension.\n",
    "- Inventory objects that belong to an installed extension.\n"
    "- Report PostGIS columns and spatial indexes from PostgreSQL core catalogs.\n",
)
replace_once(
    readme,
    "| `get_extension_objects` | List objects that belong to one extension |\n",
    "| `get_extension_objects` | List objects that belong to one extension |\n"
    "| `get_postgis_diagnostics` | Report bounded PostGIS columns and spatial indexes |\n",
)
replace_once(
    readme,
    "- Extension object inventories return at most 500 objects.\n",
    "- Extension object inventories return at most 500 objects.\n"
    "- PostGIS diagnostics return at most 500 combined columns and indexes.\n",
)

capability_test = "tests/unit/server/test_extension_profile_tools.py"
replace_once(
    capability_test,
    '        "object_inventory": {\n'
    '            "generic": True,\n'
    '            "core_catalogs_only": True,\n'
    '            "max_objects": 500,\n'
    '            "unknown_object_types": "preserved",\n'
    '        },\n'
    "    }\n",
    '        "object_inventory": {\n'
    '            "generic": True,\n'
    '            "core_catalogs_only": True,\n'
    '            "max_objects": 500,\n'
    '            "unknown_object_types": "preserved",\n'
    '        },\n'
    '        "postgis_diagnostics": {\n'
    '            "read_only": True,\n'
    '            "core_catalogs_only": True,\n'
    '            "extension_functions_called": False,\n'
    '            "max_items": 500,\n'
    '            "types": ["geometry", "geography", "raster"],\n'
    '            "index_methods": ["gist", "spgist", "brin"],\n'
    '        },\n'
    "    }\n",
)
