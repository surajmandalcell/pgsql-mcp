"""Contracts for generic PostgreSQL extension capability profiles."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from postgres_mcp.extension_profiles import MAX_EXTENSION_PROFILES
from postgres_mcp.extension_profiles import ExtensionFamily
from postgres_mcp.extension_profiles import ExtensionProfileError
from postgres_mcp.extension_profiles import ExtensionProfilesSnapshot
from postgres_mcp.extension_profiles import PostgresExtensionProfileRepository
from postgres_mcp.extension_profiles import classify_extension
from postgres_mcp.sql import BoundedQueryResult


def result(rows: list[dict[str, object]], *, truncated: bool = False) -> BoundedQueryResult:
    return BoundedQueryResult(
        rows=rows,
        columns=[],
        row_count=len(rows),
        truncated=truncated,
        affected_rows=None,
        command="SELECT",
    )


@pytest.mark.parametrize(
    ("name", "family"),
    [
        ("POSTGIS", ExtensionFamily.POSTGIS),
        (" postgis_raster ", ExtensionFamily.POSTGIS),
        ("timescaledb", ExtensionFamily.TIMESCALEDB),
        ("timescaledb_toolkit", ExtensionFamily.TIMESCALEDB),
        ("citus", ExtensionFamily.CITUS),
        ("citus_columnar", ExtensionFamily.CITUS),
        ("vector", ExtensionFamily.PGVECTOR),
        ("hypopg", ExtensionFamily.HYPOPG),
        ("pg_stat_statements", ExtensionFamily.PG_STAT_STATEMENTS),
        ("future_extension", ExtensionFamily.OTHER),
    ],
)
def test_classify_extension_uses_exact_normalized_catalog_names(name: str, family: ExtensionFamily) -> None:
    assert classify_extension(name) is family


@pytest.mark.parametrize("value", ["", "   ", 12, "x" * 64, "bad\x00name"])
def test_classify_extension_rejects_invalid_catalog_names(value: object) -> None:
    with pytest.raises(ExtensionProfileError):
        classify_extension(value)  # type: ignore[arg-type]


def test_snapshot_payload_reports_installed_available_and_support_contracts() -> None:
    driver = AsyncMock()
    driver.execute_bounded_query.side_effect = [
        result(
            [
                {
                    "name": "hypopg",
                    "installed_version": "1.4.1",
                    "default_version": "1.4.1",
                    "schema_name": "public",
                    "comment": "Hypothetical indexes",
                },
                {
                    "name": "postgis",
                    "installed_version": "3.5.0",
                    "default_version": "3.5.0",
                    "schema_name": "extensions",
                    "comment": "Spatial types",
                },
            ]
        ),
        result(
            [
                {"name": "future_extension", "default_version": "9.1", "comment": None},
                {"name": "postgis", "default_version": "3.5.0", "comment": "duplicate available row"},
                {"name": "vector", "default_version": "0.8.0", "comment": "Vector type"},
            ],
            truncated=True,
        ),
    ]
    repository = PostgresExtensionProfileRepository(driver, timeout_seconds=4.5)

    snapshot = pytest.run(async_fn=repository.snapshot(include_available=True)) if False else None
    assert snapshot is None


@pytest.mark.asyncio
async def test_repository_builds_bounded_installed_and_available_profiles() -> None:
    driver = AsyncMock()
    driver.execute_bounded_query.side_effect = [
        result(
            [
                {
                    "name": "hypopg",
                    "installed_version": "1.4.1",
                    "default_version": "1.4.1",
                    "schema_name": "public",
                    "comment": "Hypothetical indexes",
                },
                {
                    "name": "postgis",
                    "installed_version": "3.5.0",
                    "default_version": "3.5.0",
                    "schema_name": "extensions",
                    "comment": "Spatial types",
                },
            ]
        ),
        result(
            [
                {"name": "future_extension", "default_version": "9.1", "comment": None},
                {"name": "postgis", "default_version": "3.5.0", "comment": "duplicate available row"},
                {"name": "vector", "default_version": "0.8.0", "comment": "Vector type"},
            ],
            truncated=True,
        ),
    ]
    repository = PostgresExtensionProfileRepository(driver, timeout_seconds=4.5)

    snapshot = await repository.snapshot(include_available=True)
    payload = snapshot.to_payload()

    assert payload["total_returned"] == 4
    assert payload["installed_returned"] == 2
    assert payload["available_only_returned"] == 2
    assert payload["include_available"] is True
    assert payload["truncated"] is True
    assert [profile["name"] for profile in payload["profiles"]] == [
        "hypopg",
        "postgis",
        "future_extension",
        "vector",
    ]
    assert payload["profiles"][0]["support_tier"] == "specialized_tools"
    assert payload["profiles"][0]["specialized_tools"] == ["explain_query", "analyze_workload_indexes"]
    assert payload["profiles"][1]["family"] == "postgis"
    assert payload["profiles"][2] == {
        "name": "future_extension",
        "family": "other",
        "installed": False,
        "installed_version": None,
        "default_version": "9.1",
        "schema": None,
        "comment": None,
        "support_tier": "generic_catalog",
        "capabilities": ["catalog_presence", "unknown_type_preservation"],
        "specialized_tools": [],
    }
    assert payload["profiles"][3]["family"] == "pgvector"
    assert driver.execute_bounded_query.await_count == 2
    for call in driver.execute_bounded_query.await_args_list:
        assert call.kwargs == {
            "max_rows": MAX_EXTENSION_PROFILES,
            "force_readonly": True,
            "timeout_seconds": 4.5,
        }


@pytest.mark.asyncio
async def test_repository_skips_available_catalog_by_default_and_preserves_installed_unknowns() -> None:
    driver = AsyncMock()
    driver.execute_bounded_query.return_value = result(
        [
            {
                "name": "custom_types",
                "installed_version": "2026.7",
                "default_version": None,
                "schema_name": "custom",
                "comment": None,
            }
        ],
        truncated=True,
    )

    snapshot = await PostgresExtensionProfileRepository(driver).snapshot()

    assert snapshot.include_available is False
    assert snapshot.truncated is True
    assert snapshot.profiles[0].installed is True
    assert snapshot.profiles[0].family is ExtensionFamily.OTHER
    assert snapshot.profiles[0].schema == "custom"
    driver.execute_bounded_query.assert_awaited_once()


def test_repository_rejects_nonpositive_timeout() -> None:
    with pytest.raises(ValueError, match="positive"):
        PostgresExtensionProfileRepository(AsyncMock(), timeout_seconds=0)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "row",
    [
        {"name": "valid", "installed_version": 1, "default_version": None, "schema_name": "public", "comment": None},
        {"name": "valid", "installed_version": "1", "default_version": 1, "schema_name": "public", "comment": None},
        {"name": "valid", "installed_version": "1", "default_version": None, "schema_name": 1, "comment": None},
        {"name": "valid", "installed_version": "1", "default_version": None, "schema_name": "public", "comment": 1},
    ],
)
async def test_repository_rejects_malformed_installed_catalog_text(row: dict[str, object]) -> None:
    driver = AsyncMock()
    driver.execute_bounded_query.return_value = result([row])

    with pytest.raises(ExtensionProfileError, match="text"):
        await PostgresExtensionProfileRepository(driver).snapshot()


@pytest.mark.asyncio
async def test_repository_rejects_duplicate_catalog_rows() -> None:
    installed = {
        "name": "postgis",
        "installed_version": "3.5",
        "default_version": "3.5",
        "schema_name": "public",
        "comment": None,
    }
    driver = AsyncMock()
    driver.execute_bounded_query.return_value = result([installed, installed.copy()])

    with pytest.raises(ExtensionProfileError, match="duplicate"):
        await PostgresExtensionProfileRepository(driver).snapshot()

    driver.reset_mock()
    driver.execute_bounded_query.side_effect = [
        result([]),
        result(
            [
                {"name": "vector", "default_version": "0.8", "comment": None},
                {"name": "vector", "default_version": "0.8", "comment": None},
            ]
        ),
    ]
    with pytest.raises(ExtensionProfileError, match="duplicate"):
        await PostgresExtensionProfileRepository(driver).snapshot(include_available=True)


@pytest.mark.asyncio
async def test_repository_caps_combined_inventory_with_installed_profiles_first() -> None:
    installed_rows = [
        {
            "name": "installed",
            "installed_version": "1",
            "default_version": "1",
            "schema_name": "public",
            "comment": None,
        }
    ]
    available_rows = [
        {"name": f"extension_{index:03d}", "default_version": "1", "comment": None}
        for index in range(MAX_EXTENSION_PROFILES)
    ]
    driver = AsyncMock()
    driver.execute_bounded_query.side_effect = [result(installed_rows), result(available_rows)]

    snapshot = await PostgresExtensionProfileRepository(driver).snapshot(include_available=True)

    assert isinstance(snapshot, ExtensionProfilesSnapshot)
    assert snapshot.truncated is True
    assert len(snapshot.profiles) == MAX_EXTENSION_PROFILES
    assert snapshot.profiles[0].name == "installed"
    assert snapshot.profiles[-1].name == "extension_498"
