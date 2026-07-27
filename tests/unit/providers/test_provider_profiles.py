"""Contracts for conservative PostgreSQL provider capability profiles."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from postgres_mcp.provider_profiles import DeploymentProvider
from postgres_mcp.provider_profiles import DetectionConfidence
from postgres_mcp.provider_profiles import PostgresProviderProfileRepository
from postgres_mcp.provider_profiles import ProviderProfileError
from postgres_mcp.provider_profiles import parse_provider_hint
from postgres_mcp.provider_profiles import profile_from_row
from postgres_mcp.sql import BoundedQueryResult


@pytest.fixture
def base_row() -> dict[str, object]:
    return {
        "server_version_num": 180001,
        "in_recovery": False,
        "wal_level": "logical",
        "max_wal_senders": 10,
        "max_replication_slots": 10,
        "version_mentions_aurora": False,
        "version_mentions_alloydb": False,
        "has_rds_settings": False,
        "has_cloudsql_settings": False,
        "has_azure_settings": False,
        "has_neon_settings": False,
        "has_rds_superuser_role": False,
        "has_cloudsql_superuser_role": False,
        "has_azure_admin_role": False,
        "has_neon_superuser_role": False,
    }


def test_no_markers_remain_unknown_instead_of_guessing_upstream(base_row: dict[str, object]) -> None:
    snapshot = profile_from_row(base_row)

    assert snapshot.provider is DeploymentProvider.UNKNOWN
    assert snapshot.confidence is DetectionConfidence.UNKNOWN
    assert snapshot.evidence == ()
    assert snapshot.runtime.to_payload()["logical_replication_configured"] is True
    assert "supply an explicit provider hint" in snapshot.constraints.notes[0]


@pytest.mark.parametrize(
    ("field", "provider", "marker"),
    [
        ("has_rds_settings", DeploymentProvider.AWS_RDS, "rds.*"),
        ("has_cloudsql_superuser_role", DeploymentProvider.GOOGLE_CLOUD_SQL, "cloudsqlsuperuser"),
        ("has_azure_settings", DeploymentProvider.AZURE_FLEXIBLE_SERVER, "azure.*"),
        ("has_neon_superuser_role", DeploymentProvider.NEON, "neon_superuser"),
        ("version_mentions_alloydb", DeploymentProvider.GOOGLE_ALLOYDB, "alloydb"),
    ],
)
def test_strong_marker_selects_one_provider(
    base_row: dict[str, object],
    field: str,
    provider: DeploymentProvider,
    marker: str,
) -> None:
    base_row[field] = True

    snapshot = profile_from_row(base_row)

    assert snapshot.provider is provider
    assert snapshot.confidence is DetectionConfidence.HIGH
    assert snapshot.evidence[0].marker == marker
    assert snapshot.constraints.true_postgres_superuser == "unavailable"


def test_aurora_marker_wins_over_compatible_rds_markers(base_row: dict[str, object]) -> None:
    base_row["version_mentions_aurora"] = True
    base_row["has_rds_settings"] = True
    base_row["has_rds_superuser_role"] = True

    snapshot = profile_from_row(base_row)

    assert snapshot.provider is DeploymentProvider.AWS_AURORA
    assert {item.provider for item in snapshot.evidence} == {
        DeploymentProvider.AWS_AURORA,
        DeploymentProvider.AWS_RDS,
    }


def test_conflicting_marker_families_refuse_single_provider(base_row: dict[str, object]) -> None:
    base_row["has_rds_settings"] = True
    base_row["has_azure_admin_role"] = True

    snapshot = profile_from_row(base_row)

    assert snapshot.provider is DeploymentProvider.GENERIC_MANAGED
    assert snapshot.confidence is DetectionConfidence.UNKNOWN
    assert snapshot.warnings == ("multiple provider marker families were observed; no single provider was selected",)


def test_explicit_hint_is_authoritative_but_conflicts_are_visible(base_row: dict[str, object]) -> None:
    base_row["has_neon_settings"] = True

    snapshot = profile_from_row(base_row, provider_hint="aws-rds")

    assert snapshot.provider is DeploymentProvider.AWS_RDS
    assert snapshot.confidence is DetectionConfidence.EXPLICIT
    assert snapshot.explicit_hint is DeploymentProvider.AWS_RDS
    assert "conflicts with strong marker" in snapshot.warnings[0]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ("auto", None),
        ("self-hosted", DeploymentProvider.UPSTREAM),
        ("aurora", DeploymentProvider.AWS_AURORA),
        ("cloud-sql", DeploymentProvider.GOOGLE_CLOUD_SQL),
        ("supabase", DeploymentProvider.SUPABASE_HOSTED),
        (DeploymentProvider.NEON, DeploymentProvider.NEON),
    ],
)
def test_provider_hint_aliases(value: object, expected: DeploymentProvider | None) -> None:
    assert parse_provider_hint(value) is expected  # type: ignore[arg-type]


def test_invalid_hint_and_catalog_values_are_rejected(base_row: dict[str, object]) -> None:
    with pytest.raises(ProviderProfileError, match="unsupported provider hint"):
        parse_provider_hint("made-up-cloud")

    malformed = dict(base_row)
    malformed["has_rds_settings"] = 1
    with pytest.raises(ProviderProfileError, match="must be boolean"):
        profile_from_row(malformed)

    malformed = dict(base_row)
    malformed["server_version_num"] = 99999
    with pytest.raises(ProviderProfileError, match="at least 100000"):
        profile_from_row(malformed)

    malformed = dict(base_row)
    malformed["wal_level"] = "x" * 129
    with pytest.raises(ProviderProfileError, match="bounded text"):
        profile_from_row(malformed)


@pytest.mark.asyncio
async def test_repository_uses_one_bounded_read_only_query(base_row: dict[str, object]) -> None:
    driver = AsyncMock()
    driver.execute_bounded_query.return_value = BoundedQueryResult(
        rows=[base_row],
        columns=[],
        row_count=1,
        truncated=False,
        affected_rows=None,
        command="SELECT",
    )
    repository = PostgresProviderProfileRepository(driver, timeout_seconds=4)

    snapshot = await repository.snapshot(provider_hint="upstream")

    assert snapshot.provider is DeploymentProvider.UPSTREAM
    driver.execute_bounded_query.assert_awaited_once()
    kwargs = driver.execute_bounded_query.await_args.kwargs
    assert kwargs == {"max_rows": 1, "force_readonly": True, "timeout_seconds": 4}
    sql = driver.execute_bounded_query.await_args.args[0]
    normalized = " ".join(sql.split()).lower()
    assert "subconninfo" not in normalized
    assert "conninfo" not in normalized
    assert "password" not in normalized
    assert "pg_settings" in normalized
    assert "pg_roles" in normalized


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "result",
    [
        BoundedQueryResult([], [], 0, False, None, "SELECT"),
        BoundedQueryResult([{}], [], 1, True, None, "SELECT"),
        BoundedQueryResult([{}, {}], [], 2, False, None, "SELECT"),
    ],
)
async def test_repository_requires_exactly_one_untruncated_row(result: BoundedQueryResult) -> None:
    driver = AsyncMock()
    driver.execute_bounded_query.return_value = result
    repository = PostgresProviderProfileRepository(driver)

    with pytest.raises(ProviderProfileError, match="exactly one row"):
        await repository.snapshot()


def test_repository_timeout_must_be_positive() -> None:
    with pytest.raises(ValueError, match="positive"):
        PostgresProviderProfileRepository(AsyncMock(), timeout_seconds=0)
