"""Boundary contracts for conservative PostgreSQL migration classification."""

from __future__ import annotations

from collections.abc import Callable
from typing import cast

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
    behavior = cast(
        Callable[[str, str], MigrationBehavior],
        getattr(planner, "_behavior"),
    )

    assert behavior("unknown", "VACUUM") is MigrationBehavior.FORBIDDEN
    assert behavior("unknown", "CREATE DATABASE app") is MigrationBehavior.FORBIDDEN
    assert behavior("unknown", "CREATE USER MAPPING FOR app SERVER remote") is MigrationBehavior.EXTERNAL_SIDE_EFFECT
    assert behavior("unknown", "NOTIFY cache_refresh") is MigrationBehavior.EXTERNAL_SIDE_EFFECT
    assert behavior("unknown", "ANALYZE app.items") is MigrationBehavior.FORBIDDEN


def test_warning_registry_covers_scan_and_dependency_risks() -> None:
    warning_registry = cast(
        Callable[[str, str, MigrationBehavior], tuple[str, ...]],
        getattr(planner, "_warnings"),
    )
    warnings = warning_registry(
        "altertable",
        "ALTER TABLE APP.ITEMS VALIDATE CONSTRAINT ITEMS_TENANT_FKEY",
        MigrationBehavior.TRANSACTIONAL,
    )
    cascade = warning_registry(
        "droptable",
        "DROP TABLE APP.ITEMS CASCADE",
        MigrationBehavior.TRANSACTIONAL,
    )

    assert "table_scan_possible" in warnings
    assert "cascade_dependency_changes" in cascade


def test_checked_sql_rejects_generic_empty_and_oversized_input() -> None:
    checked_sql = cast(
        Callable[..., str],
        getattr(planner, "_checked_sql"),
    )

    with pytest.raises(MigrationValidationError, match="forward SQL must not be empty"):
        checked_sql(" ", label="forward SQL")
    with pytest.raises(MigrationValidationError, match="cannot exceed"):
        checked_sql(
            "x" * (MAX_MIGRATION_SQL_CHARACTERS + 1),
            label="SQL",
        )
