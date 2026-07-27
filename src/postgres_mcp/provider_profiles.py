"""Conservative PostgreSQL deployment-provider capability profiles.

The profile reads booleans and standard PostgreSQL settings only. It never reads
connection strings, host names, cloud resource identifiers, secret setting
values, or extension-owned functions. Automatic classification requires a
strong server marker; otherwise the provider remains unknown.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from typing_extensions import LiteralString

from .sql import SqlDriver

_PROVIDER_SQL: LiteralString = """
SELECT
    pg_catalog.current_setting('server_version_num')::integer AS server_version_num,
    pg_catalog.pg_is_in_recovery() AS in_recovery,
    pg_catalog.current_setting('wal_level') AS wal_level,
    pg_catalog.current_setting('max_wal_senders')::integer AS max_wal_senders,
    pg_catalog.current_setting('max_replication_slots')::integer AS max_replication_slots,
    pg_catalog.version() ILIKE '%Aurora%' AS version_mentions_aurora,
    pg_catalog.version() ILIKE '%AlloyDB%' AS version_mentions_alloydb,
    EXISTS (
        SELECT 1 FROM pg_catalog.pg_settings WHERE name LIKE 'rds.%'
    ) AS has_rds_settings,
    EXISTS (
        SELECT 1 FROM pg_catalog.pg_settings WHERE name LIKE 'cloudsql.%'
    ) AS has_cloudsql_settings,
    EXISTS (
        SELECT 1 FROM pg_catalog.pg_settings WHERE name LIKE 'azure.%'
    ) AS has_azure_settings,
    EXISTS (
        SELECT 1 FROM pg_catalog.pg_settings WHERE name LIKE 'neon.%'
    ) AS has_neon_settings,
    EXISTS (
        SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'rds_superuser'
    ) AS has_rds_superuser_role,
    EXISTS (
        SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'cloudsqlsuperuser'
    ) AS has_cloudsql_superuser_role,
    EXISTS (
        SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'azure_pg_admin'
    ) AS has_azure_admin_role,
    EXISTS (
        SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'neon_superuser'
    ) AS has_neon_superuser_role
"""


class ProviderProfileError(Exception):
    """Raised when provider profile input or trusted catalog data is invalid."""


class DeploymentProvider(str, Enum):
    """Supported explicit or strongly detectable deployment profiles."""

    UNKNOWN = "unknown"
    UPSTREAM = "upstream"
    GENERIC_MANAGED = "generic_managed"
    AWS_RDS = "aws_rds"
    AWS_AURORA = "aws_aurora"
    GOOGLE_CLOUD_SQL = "google_cloud_sql"
    GOOGLE_ALLOYDB = "google_alloydb"
    AZURE_FLEXIBLE_SERVER = "azure_flexible_server"
    NEON = "neon"
    SUPABASE_HOSTED = "supabase_hosted"


class DetectionConfidence(str, Enum):
    """How the selected provider identity was established."""

    EXPLICIT = "explicit"
    HIGH = "high"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ProviderEvidence:
    """One non-secret provider marker used by the classifier."""

    source: str
    marker: str
    provider: DeploymentProvider

    def to_payload(self) -> dict[str, str]:
        return {
            "source": self.source,
            "marker": self.marker,
            "provider": self.provider.value,
        }


@dataclass(frozen=True, slots=True)
class RuntimeCapabilities:
    """Standard PostgreSQL replication capabilities observed at runtime."""

    server_version_num: int
    in_recovery: bool
    wal_level: str
    max_wal_senders: int
    max_replication_slots: int

    def to_payload(self) -> dict[str, Any]:
        return {
            "server_version_num": self.server_version_num,
            "in_recovery": self.in_recovery,
            "wal_level": self.wal_level,
            "max_wal_senders": self.max_wal_senders,
            "max_replication_slots": self.max_replication_slots,
            "logical_replication_configured": (self.wal_level == "logical" and self.max_wal_senders > 0 and self.max_replication_slots > 0),
        }


@dataclass(frozen=True, slots=True)
class ProviderConstraints:
    """Stable operational expectations for one deployment class."""

    host_os_access: str
    true_postgres_superuser: str
    extension_installation: str
    failover_control: str
    logical_replication: str
    notes: tuple[str, ...]

    def to_payload(self) -> dict[str, Any]:
        return {
            "host_os_access": self.host_os_access,
            "true_postgres_superuser": self.true_postgres_superuser,
            "extension_installation": self.extension_installation,
            "failover_control": self.failover_control,
            "logical_replication": self.logical_replication,
            "notes": list(self.notes),
        }


@dataclass(frozen=True, slots=True)
class ProviderProfileSnapshot:
    """Answer-first provider identity, evidence, and runtime capability state."""

    provider: DeploymentProvider
    confidence: DetectionConfidence
    explicit_hint: DeploymentProvider | None
    evidence: tuple[ProviderEvidence, ...]
    constraints: ProviderConstraints
    runtime: RuntimeCapabilities
    warnings: tuple[str, ...]

    def to_payload(self) -> dict[str, Any]:
        return {
            "provider": self.provider.value,
            "confidence": self.confidence.value,
            "explicit_hint": self.explicit_hint.value if self.explicit_hint is not None else None,
            "evidence": [item.to_payload() for item in self.evidence],
            "constraints": self.constraints.to_payload(),
            "runtime": self.runtime.to_payload(),
            "warnings": list(self.warnings),
        }


_MANAGED_NOTES = (
    "Use the provider's documented administrative role instead of assuming PostgreSQL SUPERUSER.",
    "Treat extension, parameter, backup, replica, and failover operations as provider-controlled capabilities.",
)

_PROVIDER_CONSTRAINTS: dict[DeploymentProvider, ProviderConstraints] = {
    DeploymentProvider.UNKNOWN: ProviderConstraints(
        "unknown",
        "unknown",
        "inspect_runtime",
        "unknown",
        "configuration_dependent",
        ("No strong provider marker was observed; supply an explicit provider hint when operational policy depends on it.",),
    ),
    DeploymentProvider.UPSTREAM: ProviderConstraints(
        "deployment_defined",
        "deployment_defined",
        "deployment_defined",
        "deployment_defined",
        "configuration_dependent",
        ("The upstream profile is explicit; it is never inferred merely because managed-service markers are absent.",),
    ),
    DeploymentProvider.GENERIC_MANAGED: ProviderConstraints(
        "unavailable",
        "unavailable",
        "provider_allowlist",
        "provider_managed",
        "configuration_dependent",
        _MANAGED_NOTES,
    ),
    DeploymentProvider.AWS_RDS: ProviderConstraints(
        "unavailable",
        "unavailable",
        "rds_supported_and_allowlisted",
        "provider_managed",
        "parameter_and_role_dependent",
        _MANAGED_NOTES,
    ),
    DeploymentProvider.AWS_AURORA: ProviderConstraints(
        "unavailable",
        "unavailable",
        "aurora_supported_and_allowlisted",
        "provider_managed",
        "parameter_and_role_dependent",
        _MANAGED_NOTES,
    ),
    DeploymentProvider.GOOGLE_CLOUD_SQL: ProviderConstraints(
        "unavailable",
        "unavailable",
        "cloud_sql_supported_only",
        "provider_managed",
        "flag_and_role_dependent",
        _MANAGED_NOTES,
    ),
    DeploymentProvider.GOOGLE_ALLOYDB: ProviderConstraints(
        "unavailable",
        "unavailable",
        "alloydb_supported_only",
        "provider_managed",
        "configuration_dependent",
        _MANAGED_NOTES,
    ),
    DeploymentProvider.AZURE_FLEXIBLE_SERVER: ProviderConstraints(
        "unavailable",
        "unavailable",
        "azure_allowlist",
        "provider_managed",
        "parameter_and_role_dependent",
        _MANAGED_NOTES,
    ),
    DeploymentProvider.NEON: ProviderConstraints(
        "unavailable",
        "unavailable",
        "neon_supported_only",
        "provider_managed",
        "role_and_plan_dependent",
        _MANAGED_NOTES,
    ),
    DeploymentProvider.SUPABASE_HOSTED: ProviderConstraints(
        "unavailable",
        "unavailable",
        "platform_supported_only",
        "provider_managed",
        "platform_configuration_dependent",
        (
            *_MANAGED_NOTES,
            "This profile describes Supabase-hosted deployments; self-hosted Supabase should use upstream or explicit generic-managed policy.",
        ),
    ),
}


def parse_provider_hint(value: str | DeploymentProvider | None) -> DeploymentProvider | None:
    """Normalize an optional explicit provider hint."""
    if value is None:
        return None
    if isinstance(value, DeploymentProvider):
        return None if value is DeploymentProvider.UNKNOWN else value
    if not isinstance(value, str):
        raise ProviderProfileError("provider hint must be text")
    normalized = value.strip().lower().replace("-", "_")
    if normalized in {"", "auto", "unknown"}:
        return None
    aliases = {
        "rds": DeploymentProvider.AWS_RDS,
        "aurora": DeploymentProvider.AWS_AURORA,
        "cloud_sql": DeploymentProvider.GOOGLE_CLOUD_SQL,
        "alloydb": DeploymentProvider.GOOGLE_ALLOYDB,
        "azure": DeploymentProvider.AZURE_FLEXIBLE_SERVER,
        "supabase": DeploymentProvider.SUPABASE_HOSTED,
        "self_hosted": DeploymentProvider.UPSTREAM,
    }
    if normalized in aliases:
        return aliases[normalized]
    try:
        return DeploymentProvider(normalized)
    except ValueError as exc:
        supported = ", ".join(provider.value for provider in DeploymentProvider if provider is not DeploymentProvider.UNKNOWN)
        raise ProviderProfileError(f"unsupported provider hint {value!r}; expected one of: {supported}") from exc


def _required_bool(row: dict[str, Any], name: str) -> bool:
    value = row.get(name)
    if type(value) is not bool:
        raise ProviderProfileError(f"provider marker {name!r} must be boolean")
    return value


def _required_int(row: dict[str, Any], name: str, *, minimum: int = 0) -> int:
    value = row.get(name)
    if type(value) is not int or value < minimum:
        raise ProviderProfileError(f"provider runtime field {name!r} must be an integer of at least {minimum}")
    return value


def _required_text(row: dict[str, Any], name: str) -> str:
    value = row.get(name)
    if not isinstance(value, str) or not value or len(value) > 128:
        raise ProviderProfileError(f"provider runtime field {name!r} must be bounded text")
    return value


def _evidence(row: dict[str, Any]) -> tuple[ProviderEvidence, ...]:
    markers = (
        ("version", "aurora", DeploymentProvider.AWS_AURORA, "version_mentions_aurora"),
        ("version", "alloydb", DeploymentProvider.GOOGLE_ALLOYDB, "version_mentions_alloydb"),
        ("setting_prefix", "rds.*", DeploymentProvider.AWS_RDS, "has_rds_settings"),
        ("setting_prefix", "cloudsql.*", DeploymentProvider.GOOGLE_CLOUD_SQL, "has_cloudsql_settings"),
        ("setting_prefix", "azure.*", DeploymentProvider.AZURE_FLEXIBLE_SERVER, "has_azure_settings"),
        ("setting_prefix", "neon.*", DeploymentProvider.NEON, "has_neon_settings"),
        ("role", "rds_superuser", DeploymentProvider.AWS_RDS, "has_rds_superuser_role"),
        ("role", "cloudsqlsuperuser", DeploymentProvider.GOOGLE_CLOUD_SQL, "has_cloudsql_superuser_role"),
        ("role", "azure_pg_admin", DeploymentProvider.AZURE_FLEXIBLE_SERVER, "has_azure_admin_role"),
        ("role", "neon_superuser", DeploymentProvider.NEON, "has_neon_superuser_role"),
    )
    found = [ProviderEvidence(source, marker, provider) for source, marker, provider, key in markers if _required_bool(row, key)]
    return tuple(sorted(found, key=lambda item: (item.provider.value, item.source, item.marker)))


def _detected_provider(evidence: tuple[ProviderEvidence, ...]) -> DeploymentProvider:
    providers = {item.provider for item in evidence}
    if not providers:
        return DeploymentProvider.UNKNOWN
    if DeploymentProvider.AWS_AURORA in providers and providers <= {
        DeploymentProvider.AWS_AURORA,
        DeploymentProvider.AWS_RDS,
    }:
        return DeploymentProvider.AWS_AURORA
    if len(providers) == 1:
        return next(iter(providers))
    return DeploymentProvider.GENERIC_MANAGED


def profile_from_row(
    row: dict[str, Any],
    *,
    provider_hint: str | DeploymentProvider | None = None,
) -> ProviderProfileSnapshot:
    """Build a provider profile from one trusted, bounded catalog row."""
    hint = parse_provider_hint(provider_hint)
    evidence = _evidence(row)
    detected = _detected_provider(evidence)
    warnings: list[str] = []

    if hint is not None:
        provider = hint
        confidence = DetectionConfidence.EXPLICIT
        if detected not in {DeploymentProvider.UNKNOWN, DeploymentProvider.GENERIC_MANAGED, hint}:
            warnings.append(f"explicit provider hint {hint.value!r} conflicts with strong marker for {detected.value!r}")
    else:
        provider = detected
        confidence = (
            DetectionConfidence.HIGH
            if detected
            not in {
                DeploymentProvider.UNKNOWN,
                DeploymentProvider.GENERIC_MANAGED,
            }
            else DetectionConfidence.UNKNOWN
        )
        if detected is DeploymentProvider.GENERIC_MANAGED:
            warnings.append("multiple provider marker families were observed; no single provider was selected")

    runtime = RuntimeCapabilities(
        server_version_num=_required_int(row, "server_version_num", minimum=100000),
        in_recovery=_required_bool(row, "in_recovery"),
        wal_level=_required_text(row, "wal_level"),
        max_wal_senders=_required_int(row, "max_wal_senders"),
        max_replication_slots=_required_int(row, "max_replication_slots"),
    )
    return ProviderProfileSnapshot(
        provider=provider,
        confidence=confidence,
        explicit_hint=hint,
        evidence=evidence,
        constraints=_PROVIDER_CONSTRAINTS[provider],
        runtime=runtime,
        warnings=tuple(warnings),
    )


class PostgresProviderProfileRepository:
    """Read one non-secret provider capability snapshot from PostgreSQL."""

    def __init__(self, sql_driver: SqlDriver, *, timeout_seconds: float = 10.0) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.sql_driver = sql_driver
        self.timeout_seconds = timeout_seconds

    async def snapshot(
        self,
        *,
        provider_hint: str | DeploymentProvider | None = None,
    ) -> ProviderProfileSnapshot:
        result = await self.sql_driver.execute_bounded_query(
            _PROVIDER_SQL,
            max_rows=1,
            force_readonly=True,
            timeout_seconds=self.timeout_seconds,
        )
        if result.truncated or result.row_count != 1 or len(result.rows) != 1:
            raise ProviderProfileError("provider capability query must return exactly one row")
        return profile_from_row(result.rows[0], provider_hint=provider_hint)
