"""Bounded PostgreSQL extension-owned object inventory.

The repository uses PostgreSQL dependency catalogs and `pg_identify_object`, a
core pg_catalog function, to describe objects owned by any installed extension.
It never calls extension-owned functions and preserves object classes it does
not recognize.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from typing_extensions import LiteralString

from .sql import SqlDriver

MAX_EXTENSION_OBJECTS = 500

_EXTENSION_SQL: LiteralString = """
SELECT
    e.oid::bigint AS extension_oid,
    e.extname AS name,
    e.extversion AS installed_version,
    n.nspname AS schema_name,
    e.extrelocatable AS relocatable
FROM pg_catalog.pg_extension AS e
JOIN pg_catalog.pg_namespace AS n ON n.oid = e.extnamespace
WHERE e.extname = %s
"""

_OBJECTS_SQL: LiteralString = """
SELECT
    identified.type AS object_type,
    identified.schema AS schema_name,
    identified.name AS object_name,
    identified.identity,
    d.classid::pg_catalog.regclass::text AS catalog_name,
    d.objid::bigint AS object_oid,
    d.objsubid::integer AS object_sub_id
FROM pg_catalog.pg_depend AS d
CROSS JOIN LATERAL pg_catalog.pg_identify_object(d.classid, d.objid, d.objsubid) AS identified
WHERE d.refclassid = 'pg_catalog.pg_extension'::pg_catalog.regclass
  AND d.refobjid = %s
  AND d.deptype = 'e'
ORDER BY
    identified.type,
    identified.schema NULLS FIRST,
    identified.name NULLS FIRST,
    identified.identity,
    d.classid,
    d.objid,
    d.objsubid
"""


class ExtensionObjectError(Exception):
    """Raised when extension identity or object catalog data is invalid."""


@dataclass(frozen=True, slots=True)
class ExtensionIdentity:
    """Trusted installed-extension identity."""

    oid: int
    name: str
    installed_version: str
    schema: str
    relocatable: bool

    def to_payload(self) -> dict[str, Any]:
        return {
            "oid": self.oid,
            "name": self.name,
            "installed_version": self.installed_version,
            "schema": self.schema,
            "relocatable": self.relocatable,
        }


@dataclass(frozen=True, slots=True)
class ExtensionOwnedObject:
    """One PostgreSQL object recorded as extension-owned by pg_depend."""

    object_type: str
    schema: str | None
    name: str | None
    identity: str
    catalog: str
    object_oid: int
    object_sub_id: int

    def to_payload(self) -> dict[str, Any]:
        return {
            "object_type": self.object_type,
            "schema": self.schema,
            "name": self.name,
            "identity": self.identity,
            "catalog": self.catalog,
            "object_oid": self.object_oid,
            "object_sub_id": self.object_sub_id,
        }


@dataclass(frozen=True, slots=True)
class ExtensionObjectSnapshot:
    """Bounded deterministic object inventory for one installed extension."""

    extension: ExtensionIdentity
    objects: tuple[ExtensionOwnedObject, ...]
    truncated: bool

    def to_payload(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for item in self.objects:
            counts[item.object_type] = counts.get(item.object_type, 0) + 1
        return {
            "extension": self.extension.to_payload(),
            "object_count": len(self.objects),
            "object_types": dict(sorted(counts.items())),
            "truncated": self.truncated,
            "objects": [item.to_payload() for item in self.objects],
        }


def validate_extension_name(value: str) -> str:
    """Validate an exact PostgreSQL extension catalog name."""
    if not isinstance(value, str) or not value or not value.strip():
        raise ExtensionObjectError("extension name must be a non-empty string")
    normalized = value.strip().lower()
    if "\x00" in normalized or len(normalized.encode("utf-8")) > 63:
        raise ExtensionObjectError("extension name is not a valid PostgreSQL identifier")
    return normalized


def _required_text(row: dict[str, Any], field: str, *, maximum: int = 4096) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ExtensionObjectError(f"extension object field {field!r} must be bounded text")
    return value


def _optional_text(row: dict[str, Any], field: str, *, maximum: int = 4096) -> str | None:
    value = row.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > maximum:
        raise ExtensionObjectError(f"extension object field {field!r} must be bounded text when present")
    return value


def _required_int(row: dict[str, Any], field: str, *, minimum: int = 0) -> int:
    value = row.get(field)
    if type(value) is not int or value < minimum:
        raise ExtensionObjectError(f"extension object field {field!r} must be an integer of at least {minimum}")
    return value


def _extension_from_row(row: dict[str, Any]) -> ExtensionIdentity:
    relocatable = row.get("relocatable")
    if type(relocatable) is not bool:
        raise ExtensionObjectError("extension field 'relocatable' must be boolean")
    return ExtensionIdentity(
        oid=_required_int(row, "extension_oid", minimum=1),
        name=validate_extension_name(_required_text(row, "name", maximum=63)),
        installed_version=_required_text(row, "installed_version", maximum=256),
        schema=_required_text(row, "schema_name", maximum=63),
        relocatable=relocatable,
    )


def _object_from_row(row: dict[str, Any]) -> ExtensionOwnedObject:
    return ExtensionOwnedObject(
        object_type=_required_text(row, "object_type", maximum=256),
        schema=_optional_text(row, "schema_name", maximum=63),
        name=_optional_text(row, "object_name", maximum=1024),
        identity=_required_text(row, "identity"),
        catalog=_required_text(row, "catalog_name", maximum=128),
        object_oid=_required_int(row, "object_oid", minimum=1),
        object_sub_id=_required_int(row, "object_sub_id"),
    )


class PostgresExtensionObjectRepository:
    """Read extension-owned objects from trusted dependency catalogs."""

    def __init__(self, sql_driver: SqlDriver, *, timeout_seconds: float = 10.0) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.sql_driver = sql_driver
        self.timeout_seconds = timeout_seconds

    async def snapshot(self, extension_name: str, *, limit: int = 100) -> ExtensionObjectSnapshot:
        normalized = validate_extension_name(extension_name)
        if not 1 <= limit <= MAX_EXTENSION_OBJECTS:
            raise ExtensionObjectError(f"limit must be between 1 and {MAX_EXTENSION_OBJECTS}")

        extension_result = await self.sql_driver.execute_bounded_query(
            _EXTENSION_SQL,
            params=[normalized],
            max_rows=1,
            force_readonly=True,
            timeout_seconds=self.timeout_seconds,
        )
        if extension_result.truncated or extension_result.row_count != 1 or len(extension_result.rows) != 1:
            if extension_result.row_count == 0 and not extension_result.rows:
                raise ExtensionObjectError(f"extension {normalized!r} is not installed")
            raise ExtensionObjectError("extension catalog query must return exactly one row")
        extension = _extension_from_row(extension_result.rows[0])

        object_result = await self.sql_driver.execute_bounded_query(
            _OBJECTS_SQL,
            params=[extension.oid],
            max_rows=limit,
            force_readonly=True,
            timeout_seconds=self.timeout_seconds,
        )
        objects = tuple(_object_from_row(row) for row in object_result.rows)
        identities = [(item.catalog, item.object_oid, item.object_sub_id) for item in objects]
        if len(identities) != len(set(identities)):
            raise ExtensionObjectError("duplicate extension-owned object address")
        return ExtensionObjectSnapshot(extension, objects, object_result.truncated)
