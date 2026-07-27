"""Read-only PostGIS column and spatial-index diagnostics from core catalogs."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from typing_extensions import LiteralString

from .sql import SqlDriver

MAX_POSTGIS_ITEMS = 500
_MAX_TEXT = 4096

_IDENTITY_SQL: LiteralString = """
SELECT
    e.oid::bigint AS extension_oid,
    e.extversion AS installed_version,
    n.nspname AS schema_name
FROM pg_catalog.pg_extension AS e
JOIN pg_catalog.pg_namespace AS n ON n.oid = e.extnamespace
WHERE e.extname = 'postgis'
"""

_COLUMNS_SQL: LiteralString = """
SELECT
    relation_namespace.nspname AS schema_name,
    relation.relname AS relation_name,
    attribute.attname AS column_name,
    type.typname AS type_name,
    pg_catalog.format_type(attribute.atttypid, attribute.atttypmod) AS formatted_type,
    NOT attribute.attnotnull AS nullable
FROM pg_catalog.pg_attribute AS attribute
JOIN pg_catalog.pg_class AS relation ON relation.oid = attribute.attrelid
JOIN pg_catalog.pg_namespace AS relation_namespace ON relation_namespace.oid = relation.relnamespace
JOIN pg_catalog.pg_type AS type ON type.oid = attribute.atttypid
JOIN pg_catalog.pg_depend AS dependency
  ON dependency.classid = 'pg_catalog.pg_type'::pg_catalog.regclass
 AND dependency.objid = type.oid
 AND dependency.objsubid = 0
 AND dependency.refclassid = 'pg_catalog.pg_extension'::pg_catalog.regclass
 AND dependency.refobjid = %s
 AND dependency.deptype = 'e'
WHERE attribute.attnum > 0
  AND NOT attribute.attisdropped
  AND relation.relkind IN ('r', 'p', 'v', 'm', 'f')
ORDER BY relation_namespace.nspname, relation.relname, attribute.attnum
"""

_INDEXES_SQL: LiteralString = """
WITH extension_members AS (
    SELECT dependency.classid, dependency.objid
    FROM pg_catalog.pg_depend AS dependency
    WHERE dependency.refclassid = 'pg_catalog.pg_extension'::pg_catalog.regclass
      AND dependency.refobjid = %s
      AND dependency.deptype = 'e'
)
SELECT
    relation_namespace.nspname AS schema_name,
    relation.relname AS relation_name,
    index_relation.relname AS index_name,
    access_method.amname AS access_method,
    index.indisunique AS is_unique,
    index.indisvalid AS is_valid,
    index.indisready AS is_ready,
    pg_catalog.pg_get_expr(index.indpred, index.indrelid, true) AS predicate,
    pg_catalog.pg_get_expr(index.indexprs, index.indrelid, true) AS expression,
    pg_catalog.pg_get_indexdef(index.indexrelid, 0, true) AS definition,
    ARRAY(
        SELECT operator_class.opcname
        FROM unnest(index.indclass::oid[]) WITH ORDINALITY AS key(operator_class_oid, position)
        JOIN pg_catalog.pg_opclass AS operator_class ON operator_class.oid = key.operator_class_oid
        ORDER BY key.position
    ) AS operator_classes,
    index_relation.reloptions
FROM pg_catalog.pg_index AS index
JOIN pg_catalog.pg_class AS index_relation ON index_relation.oid = index.indexrelid
JOIN pg_catalog.pg_class AS relation ON relation.oid = index.indrelid
JOIN pg_catalog.pg_namespace AS relation_namespace ON relation_namespace.oid = relation.relnamespace
JOIN pg_catalog.pg_am AS access_method ON access_method.oid = index_relation.relam
WHERE EXISTS (
    SELECT 1
    FROM extension_members AS member
    WHERE member.classid = 'pg_catalog.pg_opclass'::pg_catalog.regclass
      AND member.objid = ANY(index.indclass)
)
ORDER BY relation_namespace.nspname, relation.relname, index_relation.relname
"""

_SPATIAL_TYPE = re.compile(
    r'^(?:(?:"[^"]+"|[A-Za-z_][A-Za-z0-9_$]*)\.)?'
    r'(geometry|geography|raster)(?:\(([A-Za-z0-9_]+),(-?[0-9]+)\))?$'
)

_KNOWN_OPERATOR_CLASSES = frozenset(
    {
        "brin_geography_inclusion_ops",
        "brin_geometry_inclusion_ops_2d",
        "brin_geometry_inclusion_ops_3d",
        "brin_geometry_inclusion_ops_4d",
        "gist_geography_ops",
        "gist_geometry_ops_2d",
        "gist_geometry_ops_nd",
        "gist_raster_ops",
        "hash_geometry_ops",
        "spgist_geography_ops_nd",
        "spgist_geometry_ops_2d",
        "spgist_geometry_ops_nd",
    }
)


class PostgisCatalogError(Exception):
    """Raised when PostGIS catalog data is missing, malformed, or ambiguous."""


@dataclass(frozen=True, slots=True)
class SpatialTypmod:
    """Parsed PostGIS type-modifier metadata."""

    base_type: str
    shape: str | None
    srid: int | None
    dimensions: int | None


@dataclass(frozen=True, slots=True)
class PostgisIdentity:
    """Installed PostGIS extension identity."""

    oid: int
    installed_version: str
    schema: str

    def to_payload(self) -> dict[str, Any]:
        return {"oid": self.oid, "installed_version": self.installed_version, "schema": self.schema}


@dataclass(frozen=True, slots=True)
class PostgisColumn:
    """One relation column that uses a PostGIS-owned data type."""

    schema: str
    relation: str
    column: str
    type_name: str
    formatted_type: str
    shape: str | None
    srid: int | None
    dimensions: int | None
    nullable: bool

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "relation": self.relation,
            "column": self.column,
            "type": self.type_name,
            "formatted_type": self.formatted_type,
            "shape": self.shape,
            "srid": self.srid,
            "dimensions": self.dimensions,
            "nullable": self.nullable,
        }


@dataclass(frozen=True, slots=True)
class PostgisIndex:
    """One index that uses a PostGIS-owned operator class."""

    schema: str
    relation: str
    name: str
    access_method: str
    unique: bool
    valid: bool
    ready: bool
    predicate: str | None
    expression: str | None
    definition: str
    operator_classes: tuple[str, ...]
    options: dict[str, str]

    @property
    def qualified_name(self) -> str:
        return f"{self.schema}.{self.name}"

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "relation": self.relation,
            "name": self.name,
            "access_method": self.access_method,
            "unique": self.unique,
            "valid": self.valid,
            "ready": self.ready,
            "predicate": self.predicate,
            "expression": self.expression,
            "definition": self.definition,
            "operator_classes": list(self.operator_classes),
            "options": dict(self.options),
        }


@dataclass(frozen=True, slots=True)
class PostgisFinding:
    """One deterministic PostGIS catalog finding."""

    code: str
    severity: str
    object_name: str

    def to_payload(self) -> dict[str, str]:
        return {"code": self.code, "severity": self.severity, "object": self.object_name}


@dataclass(frozen=True, slots=True)
class PostgisSnapshot:
    """Bounded PostGIS catalog snapshot."""

    identity: PostgisIdentity
    columns: tuple[PostgisColumn, ...]
    indexes: tuple[PostgisIndex, ...]
    findings: tuple[PostgisFinding, ...]
    truncated: bool

    def to_payload(self) -> dict[str, Any]:
        return {
            "extension": self.identity.to_payload(),
            "column_count": len(self.columns),
            "index_count": len(self.indexes),
            "truncated": self.truncated,
            "columns": [column.to_payload() for column in self.columns],
            "indexes": [index.to_payload() for index in self.indexes],
            "findings": [finding.to_payload() for finding in self.findings],
        }


def parse_spatial_typmod(formatted_type: str) -> SpatialTypmod:
    """Parse PostGIS typmod metadata from trusted format_type output."""
    if not isinstance(formatted_type, str) or not formatted_type or len(formatted_type) > 256:
        raise PostgisCatalogError("spatial type must be bounded text")
    match = _SPATIAL_TYPE.fullmatch(formatted_type.strip())
    if match is None:
        if re.search(r"(?:^|\.)(?:geometry|geography|raster)\(", formatted_type):
            raise PostgisCatalogError("spatial type modifier is malformed")
        raise PostgisCatalogError("formatted type is not a supported PostGIS type")

    base_type = match.group(1)
    shape_token = match.group(2)
    srid_token = match.group(3)
    if shape_token is None:
        return SpatialTypmod(base_type, None, None, None)
    if base_type == "raster" or srid_token is None:
        raise PostgisCatalogError("spatial type modifier is malformed")

    upper = shape_token.upper()
    dimensions = 2
    if upper.endswith("ZM"):
        upper = upper[:-2]
        dimensions = 4
    elif upper.endswith(("Z", "M")):
        upper = upper[:-1]
        dimensions = 3
    if not upper:
        raise PostgisCatalogError("spatial type shape is malformed")
    srid = int(srid_token)
    if srid < -1:
        raise PostgisCatalogError("spatial type SRID is invalid")
    return SpatialTypmod(base_type, upper, srid, dimensions)


def _text(row: dict[str, Any], field: str, *, maximum: int = _MAX_TEXT) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value or len(value) > maximum or "\x00" in value:
        raise PostgisCatalogError(f"PostGIS catalog field {field!r} must be bounded text")
    return value


def _optional_text(row: dict[str, Any], field: str, *, maximum: int = _MAX_TEXT) -> str | None:
    value = row.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > maximum or "\x00" in value:
        raise PostgisCatalogError(f"PostGIS catalog field {field!r} must be bounded text when present")
    return value


def _integer(row: dict[str, Any], field: str, *, minimum: int = 0) -> int:
    value = row.get(field)
    if type(value) is not int or value < minimum:
        raise PostgisCatalogError(f"PostGIS catalog field {field!r} must be an integer of at least {minimum}")
    return value


def _boolean(row: dict[str, Any], field: str) -> bool:
    value = row.get(field)
    if type(value) is not bool:
        raise PostgisCatalogError(f"PostGIS catalog field {field!r} must be boolean")
    return value


def _text_list(row: dict[str, Any], field: str) -> tuple[str, ...]:
    value = row.get(field)
    if value is None:
        return ()
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise PostgisCatalogError(f"PostGIS catalog field {field!r} must be a text array")
    return tuple(value)


def _options(row: dict[str, Any]) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for item in _text_list(row, "reloptions"):
        key, separator, value = item.partition("=")
        if not separator or not key or key in parsed:
            raise PostgisCatalogError("PostGIS index reloptions must contain unique key=value entries")
        parsed[key] = value
    return dict(sorted(parsed.items()))


def _identity(row: dict[str, Any]) -> PostgisIdentity:
    return PostgisIdentity(
        _integer(row, "extension_oid", minimum=1),
        _text(row, "installed_version", maximum=256),
        _text(row, "schema_name", maximum=63),
    )


def _column(row: dict[str, Any]) -> PostgisColumn:
    type_name = _text(row, "type_name", maximum=63)
    if type_name not in {"geometry", "geography", "raster"}:
        raise PostgisCatalogError("PostGIS column type is not supported")
    formatted_type = _text(row, "formatted_type", maximum=256)
    typmod = parse_spatial_typmod(formatted_type)
    return PostgisColumn(
        schema=_text(row, "schema_name", maximum=63),
        relation=_text(row, "relation_name", maximum=63),
        column=_text(row, "column_name", maximum=63),
        type_name=type_name,
        formatted_type=formatted_type,
        shape=typmod.shape,
        srid=typmod.srid,
        dimensions=typmod.dimensions,
        nullable=_boolean(row, "nullable"),
    )


def _index(row: dict[str, Any]) -> PostgisIndex:
    return PostgisIndex(
        schema=_text(row, "schema_name", maximum=63),
        relation=_text(row, "relation_name", maximum=63),
        name=_text(row, "index_name", maximum=63),
        access_method=_text(row, "access_method", maximum=63),
        unique=_boolean(row, "is_unique"),
        valid=_boolean(row, "is_valid"),
        ready=_boolean(row, "is_ready"),
        predicate=_optional_text(row, "predicate"),
        expression=_optional_text(row, "expression"),
        definition=_text(row, "definition"),
        operator_classes=_text_list(row, "operator_classes"),
        options=_options(row),
    )


def _findings(indexes: tuple[PostgisIndex, ...]) -> tuple[PostgisFinding, ...]:
    findings: list[PostgisFinding] = []
    for index in indexes:
        if not index.valid:
            findings.append(PostgisFinding("postgis_index_not_valid", "error", index.qualified_name))
        if not index.ready:
            findings.append(PostgisFinding("postgis_index_not_ready", "warning", index.qualified_name))
        if index.predicate is not None:
            findings.append(PostgisFinding("postgis_partial_index", "info", index.qualified_name))
        if any(operator_class not in _KNOWN_OPERATOR_CLASSES for operator_class in index.operator_classes):
            findings.append(PostgisFinding("postgis_unknown_operator_class", "info", index.qualified_name))
    return tuple(findings)


class PostgresPostgisRepository:
    """Read PostGIS metadata without executing spatial functions."""

    def __init__(self, sql_driver: SqlDriver, *, timeout_seconds: float = 10.0) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.sql_driver = sql_driver
        self.timeout_seconds = timeout_seconds

    @staticmethod
    def _validate_limits(max_columns: int, max_indexes: int) -> None:
        if max_columns <= 0 or max_indexes <= 0 or max_columns + max_indexes > MAX_POSTGIS_ITEMS:
            raise PostgisCatalogError(f"combined item limit must be between 2 and {MAX_POSTGIS_ITEMS}")

    @staticmethod
    def _snapshot_from_rows(
        identity_row: dict[str, Any],
        column_rows: list[dict[str, Any]],
        index_rows: list[dict[str, Any]],
        *,
        truncated: bool,
    ) -> PostgisSnapshot:
        identity = _identity(identity_row)
        columns = tuple(_column(row) for row in column_rows)
        indexes = tuple(_index(row) for row in index_rows)
        column_keys = [(column.schema, column.relation, column.column) for column in columns]
        if len(column_keys) != len(set(column_keys)):
            raise PostgisCatalogError("duplicate PostGIS column identity")
        index_keys = [(index.schema, index.name) for index in indexes]
        if len(index_keys) != len(set(index_keys)):
            raise PostgisCatalogError("duplicate PostGIS index identity")
        return PostgisSnapshot(identity, columns, indexes, _findings(indexes), truncated)

    async def snapshot(self, *, max_columns: int = 250, max_indexes: int = 250) -> PostgisSnapshot:
        self._validate_limits(max_columns, max_indexes)
        identity_result = await self.sql_driver.execute_bounded_query(
            _IDENTITY_SQL,
            max_rows=1,
            force_readonly=True,
            timeout_seconds=self.timeout_seconds,
        )
        if identity_result.row_count == 0 and not identity_result.rows:
            raise PostgisCatalogError("PostGIS extension is not installed")
        if identity_result.truncated or identity_result.row_count != 1 or len(identity_result.rows) != 1:
            raise PostgisCatalogError("PostGIS extension query must return exactly one row")
        identity = _identity(identity_result.rows[0])
        column_result = await self.sql_driver.execute_bounded_query(
            _COLUMNS_SQL,
            params=[identity.oid],
            max_rows=max_columns,
            force_readonly=True,
            timeout_seconds=self.timeout_seconds,
        )
        index_result = await self.sql_driver.execute_bounded_query(
            _INDEXES_SQL,
            params=[identity.oid],
            max_rows=max_indexes,
            force_readonly=True,
            timeout_seconds=self.timeout_seconds,
        )
        return self._snapshot_from_rows(
            identity_result.rows[0],
            column_result.rows,
            index_result.rows,
            truncated=column_result.truncated or index_result.truncated,
        )
