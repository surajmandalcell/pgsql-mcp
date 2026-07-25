"""Boundary contracts for conservative PostgreSQL migration classification."""

from __future__ import annotations

import pytest

from postgres_mcp.migrations import planner
from postgres_mcp.migrations.domain import MAX_MIGRATION_SQL_CHARACTERS
from postgres_mcp.migrations.domain import MAX_MIGRATION_TOTAL_CHARACTERS
from postgres_mcp.migrations.domain import MigrationBehavior
from postgres_mcp.migrations.domain import MigrationStepDraft
from postgres_mcp.migrations.domain import MigrationValidationError
from postgres_mcp.migrations.planner import MigrationPlanner


def test_total_sql_budget_is_checked_before_parsing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        planner,
        "classify_migration_sql",
        lambda _sql: ("comment", MigrationBehavior.TRANSACTIONAL, ()),
    )
    chunk = "x" * MAX_MIGRATION_SQL_CHARACTERS
    steps = [MigrationStepDraft(chunk, chunk) for _ in range(6)]

    with pytest.raises(MigrationValidationError, match=str(MAX_MIGRATION_TOTAL_CHARACTERS)):
        MigrationPlanner().create_plan(name="over-total-budget", steps=steps)


def test_behavior_registry_covers_explicit_and_default_denials() -> None:
    assert planner._behavior("unknown", "VACUUM") is MigrationBehavior.FORBIDDEN  # pyright: ignore[reportPrivateUsage]
    assert planner._behavior("unknown", "CREATE DATABASE app") is MigrationBehavior.FORBIDDEN  # pyright: ignore[reportPrivateUsage]
    assert (  # pyright: ignore[reportPrivateUsage]
        planner._behavior("unknown", "CREATE USER MAPPING FOR app SERVER remote") is MigrationBehavior.EXTERNAL_SIDE_EFFECT
    )
    assert planner._behavior("unknown", "NOTIFY cache_refresh") is MigrationBehavior.EXTERNAL_SIDE_EFFECT  # pyright: ignore[reportPrivateUsage]
    assert planner._behavior("unknown", "ANALYZE app.items") is MigrationBehavior.FORBIDDEN  # pyright: ignore[reportPrivateUsage]


def test_warning_registry_covers_scan_and_dependency_risks() -> None:
    warnings = planner._warnings(  # pyright: ignore[reportPrivateUsage]
        "altertable",
        "ALTER TABLE APP.ITEMS VALIDATE CONSTRAINT ITEMS_TENANT_FKEY",
        MigrationBehavior.TRANSACTIONAL,
    )
    cascade = planner._warnings(  # pyright: ignore[reportPrivateUsage]
        "droptable",
        "DROP TABLE APP.ITEMS CASCADE",
        MigrationBehavior.TRANSACTIONAL,
    )

    assert "table_scan_possible" in warnings
    assert "cascade_dependency_changes" in cascade


def test_checked_sql_rejects_generic_empty_and_oversized_input() -> None:
    with pytest.raises(MigrationValidationError, match="forward SQL must not be empty"):
        planner._checked_sql(" ", label="forward SQL")  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(MigrationValidationError, match="cannot exceed"):
        planner._checked_sql(  # pyright: ignore[reportPrivateUsage]
            "x" * (MAX_MIGRATION_SQL_CHARACTERS + 1),
            label="SQL",
        )
