"""Generic PostgreSQL extension capability profiles.

The module never imports extension client libraries and never executes
extension-owned functions. It reads PostgreSQL's trusted extension catalogs,
preserves unknown extensions, and reports the level of support pgsql-mcp can
honestly provide.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any
from typing_extensions import LiteralString

from .sql import BoundedQueryResult
from .sql import SqlDriver

MAX_EXTENSION_PROFILES = 500

_INSTALLED_SQL: LiteralString = """
SELECT
    e.extname AS name,
    e.extversion AS installed_version,
    n.nspname AS schema_name,
    a.default_version,
    a.comment
FROM pg_catalog.pg_extension AS e
JOIN pg_catalog.pg_namespace AS n ON n.oid = e.extnamespace
LEFT JOIN pg_catalog.pg_available_extensions AS a ON a.name = e.extname
ORDER BY e.extname
"""

_AVAILABLE_SQL: LiteralString = """
SELECT
    a.name,
    a.default_version,
    a.comment
FROM pg_catalog.pg_available_extensions AS a
WHERE a.installed_version IS NULL
ORDER BY a.name
"""


class ExtensionProfileError(Exception):
    """Raised when trusted extension catalog data is malformed or unavailable."""


class ExtensionFamily(str, Enum):
    """Known extension ecosystems with explicit pgsql-mcp support contracts."""

    POSTGIS = "postgis"
    TIMESCALEDB = "timescaledb"
    CITUS = "citus"
    PGVECTOR = "pgvector"
    HYPOPG = "hypopg"
    PG_STAT_STATEMENTS = "pg_stat_statements"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class ExtensionProfile:
    """One installed or available extension and its bounded support contract."""

    name: str
    family: ExtensionFamily
    installed: bool
    installed_version: str | None
    default_version: str | None
    schema: str | None
    comment: str | None
    support_tier: str
    capabilities: tuple[str, ...]
    specialized_tools: tuple[str, ...]

    def to_payload(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "family": self.family.value,
            "installed": self.installed,
            "installed_version": self.installed_version,
            "default_version": self.default_version,
            "schema": self.schema,
            "comment": self.comment,
            "support_tier": self.support_tier,
            "capabilities": list(self.capabilities),
            "specialized_tools": list(self.specialized_tools),
        }


@dataclass(frozen=True, slots=True)
class ExtensionProfilesSnapshot:
    """Deterministic bounded extension inventory."""

    profiles: tuple[ExtensionProfile, ...]
    include_available: bool
    truncated: bool

    def to_payload(self) -> dict[str, Any]:
        installed = sum(profile.installed for profile in self.profiles)
        return {
            "total_returned": len(self.profiles),
            "installed_returned": installed,
            "available_only_returned": len(self.profiles) - installed,
            "include_available": self.include_available,
            "truncated": self.truncated,
            "profiles": [profile.to_payload() for profile in self.profiles],
        }


@dataclass(frozen=True, slots=True)
class _CatalogExtension:
    name: str
    installed_version: str | None
    default_version: str | None
    schema: str | None
    comment: str | None


_FAMILY_RULES: tuple[tuple[ExtensionFamily, frozenset[str], tuple[str, ...], tuple[str, ...], str], ...] = (
    (
        ExtensionFamily.POSTGIS,
        frozenset({"postgis", "postgis_raster", "postgis_topology", "postgis_tiger_geocoder"}),
        ("spatial_types", "spatial_catalogs", "spatial_index_metadata", "unknown_type_preservation"),
        (),
        "catalog_and_type_compatible",
    ),
    (
        ExtensionFamily.TIMESCALEDB,
        frozenset({"timescaledb", "timescaledb_toolkit"}),
        ("hypertable_metadata", "chunk_metadata", "continuous_aggregate_metadata", "unknown_type_preservation"),
        (),
        "catalog_and_type_compatible",
    ),
    (
        ExtensionFamily.CITUS,
        frozenset({"citus", "citus_columnar"}),
        ("distribution_metadata", "shard_metadata", "worker_metadata", "unknown_type_preservation"),
        (),
        "catalog_and_type_compatible",
    ),
    (
        ExtensionFamily.PGVECTOR,
        frozenset({"vector"}),
        ("vector_type", "vector_index_metadata", "unknown_type_preservation"),
        (),
        "catalog_and_type_compatible",
    ),
    (
        ExtensionFamily.HYPOPG,
        frozenset({"hypopg"}),
        ("hypothetical_indexes",),
        ("explain_query", "analyze_workload_indexes"),
        "specialized_tools",
    ),
    (
        ExtensionFamily.PG_STAT_STATEMENTS,
        frozenset({"pg_stat_statements"}),
        ("workload_statistics",),
        ("get_top_queries", "analyze_workload_indexes"),
        "specialized_tools",
    ),
)


def classify_extension(name: str) -> ExtensionFamily:
    """Classify an extension by exact normalized catalog name."""
    normalized = _checked_name(name)
    for family, names, _capabilities, _tools, _tier in _FAMILY_RULES:
        if normalized in names:
            return family
    return ExtensionFamily.OTHER


def _support_contract(name: str) -> tuple[ExtensionFamily, str, tuple[str, ...], tuple[str, ...]]:
    family = classify_extension(name)
    for candidate, _names, capabilities, tools, tier in _FAMILY_RULES:
        if candidate is family:
            return family, tier, capabilities, tools
    return family, "generic_catalog", ("catalog_presence", "unknown_type_preservation"), ()


def _checked_name(value: object) -> str:
    if not isinstance(value, str) or not value or not value.strip():
        raise ExtensionProfileError("extension name must be a non-empty string")
    normalized = value.strip().lower()
    if len(normalized.encode("utf-8")) > 63 or "\x00" in normalized:
        raise ExtensionProfileError("extension name is not a valid PostgreSQL identifier")
    return normalized


def _optional_text(value: object, *, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ExtensionProfileError(f"{field_name} must be text when present")
    return value


def _installed_from_row(row: dict[str, Any]) -> _CatalogExtension:
    return _CatalogExtension(
        name=_checked_name(row.get("name")),
        installed_version=_optional_text(row.get("installed_version"), field_name="installed_version"),
        default_version=_optional_text(row.get("default_version"), field_name="default_version"),
        schema=_optional_text(row.get("schema_name"), field_name="schema_name"),
        comment=_optional_text(row.get("comment"), field_name="comment"),
    )


def _available_from_row(row: dict[str, Any]) -> _CatalogExtension:
    return _CatalogExtension(
        name=_checked_name(row.get("name")),
        installed_version=None,
        default_version=_optional_text(row.get("default_version"), field_name="default_version"),
        schema=None,
        comment=_optional_text(row.get("comment"), field_name="comment"),
    )


def _profile(extension: _CatalogExtension) -> ExtensionProfile:
    family, tier, capabilities, tools = _support_contract(extension.name)
    return ExtensionProfile(
        name=extension.name,
        family=family,
        installed=extension.installed_version is not None,
        installed_version=extension.installed_version,
        default_version=extension.default_version,
        schema=extension.schema,
        comment=extension.comment,
        support_tier=tier,
        capabilities=capabilities,
        specialized_tools=tools,
    )


class PostgresExtensionProfileRepository:
    """Read bounded extension capability profiles from trusted catalogs."""

    def __init__(self, sql_driver: SqlDriver, *, timeout_seconds: float = 10.0) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.sql_driver = sql_driver
        self.timeout_seconds = timeout_seconds

    async def snapshot(self, *, include_available: bool = False) -> ExtensionProfilesSnapshot:
        installed_result = await self.sql_driver.execute_bounded_query(
            _INSTALLED_SQL,
            max_rows=MAX_EXTENSION_PROFILES,
            force_readonly=True,
            timeout_seconds=self.timeout_seconds,
        )
        installed = self._unique(installed_result, installed=True)

        available: dict[str, _CatalogExtension] = {}
        available_truncated = False
        if include_available:
            available_result = await self.sql_driver.execute_bounded_query(
                _AVAILABLE_SQL,
                max_rows=MAX_EXTENSION_PROFILES,
                force_readonly=True,
                timeout_seconds=self.timeout_seconds,
            )
            available = self._unique(available_result, installed=False)
            available_truncated = available_result.truncated

        ordered = [installed[name] for name in sorted(installed)]
        ordered.extend(available[name] for name in sorted(available) if name not in installed)
        truncated = installed_result.truncated or available_truncated or len(ordered) > MAX_EXTENSION_PROFILES
        profiles = tuple(_profile(extension) for extension in ordered[:MAX_EXTENSION_PROFILES])
        return ExtensionProfilesSnapshot(profiles, include_available, truncated)

    @staticmethod
    def _unique(result: BoundedQueryResult, *, installed: bool) -> dict[str, _CatalogExtension]:
        parsed: dict[str, _CatalogExtension] = {}
        for row in result.rows:
            extension = _installed_from_row(row) if installed else _available_from_row(row)
            if extension.name in parsed:
                raise ExtensionProfileError(f"duplicate extension catalog row for {extension.name!r}")
            parsed[extension.name] = extension
        return parsed
