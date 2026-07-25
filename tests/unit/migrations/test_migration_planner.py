"""Test-first contracts for the reviewed migration aggregate."""

from __future__ import annotations

from dataclasses import replace

import pytest

from postgres_mcp.migrations.domain import MAX_MIGRATION_STEPS
from postgres_mcp.migrations.domain import MigrationBehavior
from postgres_mcp.migrations.domain import MigrationPlan
from postgres_mcp.migrations.domain import MigrationStepDraft
from postgres_mcp.migrations.domain import MigrationValidationError
from postgres_mcp.migrations.planner import MigrationPlanner
from postgres_mcp.migrations.planner import classify_migration_sql


def draft(sql: str, rollback_sql: str) -> MigrationStepDraft:
    return MigrationStepDraft(sql=sql, rollback_sql=rollback_sql)


def test_plan_hash_is_deterministic_and_binds_order_compensation_and_policy() -> None:
    planner = MigrationPlanner()
    steps = [
        draft("CREATE TABLE app.items (id bigint PRIMARY KEY)", "DROP TABLE app.items"),
        draft("CREATE INDEX items_id_idx ON app.items (id)", "DROP INDEX app.items_id_idx"),
    ]

    first = planner.create_plan(name="2026-07-24.create-items", steps=steps)
    second = planner.create_plan(name="2026-07-24.create-items", steps=steps)
    reversed_plan = planner.create_plan(name="2026-07-24.create-items", steps=list(reversed(steps)))
    changed_rollback = planner.create_plan(
        name="2026-07-24.create-items",
        steps=[steps[0], draft(steps[1].sql, "DROP INDEX IF EXISTS app.items_id_idx")],
    )

    assert first.review_hash == second.review_hash
    assert first.checksum == second.checksum
    assert first.review_hash != first.checksum
    assert first.review_hash != reversed_plan.review_hash
    assert first.review_hash != changed_rollback.review_hash
    assert first.applyable is True
    assert first.reversible is True
    assert "rollback:destructive_drop" in first.warnings
    first.assert_integrity()


def test_plan_integrity_rejects_tampered_hashes_or_content() -> None:
    plan = MigrationPlanner().create_plan(
        name="create-items",
        steps=[draft("CREATE TABLE app.items(id integer)", "DROP TABLE app.items")],
    )

    with pytest.raises(MigrationValidationError, match="checksum"):
        replace(plan, checksum="0" * 64).assert_integrity()
    with pytest.raises(MigrationValidationError, match="canonical content"):
        replace(plan, steps=(replace(plan.steps[0], sql="CREATE TABLE app.other(id integer)"),)).assert_integrity()


def test_canonical_plan_round_trip_verifies_ledger_content() -> None:
    plan = MigrationPlanner().create_plan(
        name="create-items",
        steps=[draft("CREATE TABLE app.items(id integer)", "DROP TABLE app.items")],
    )

    restored = MigrationPlan.from_canonical_payload(
        plan.canonical_payload(),
        checksum=plan.checksum,
        review_hash=plan.review_hash,
    )

    assert restored == plan
    corrupted = plan.canonical_payload()
    corrupted["steps"][0]["rollback_warnings"] = ["invented_warning"]
    with pytest.raises(MigrationValidationError, match="review_hash"):
        MigrationPlan.from_canonical_payload(
            corrupted,
            checksum=plan.checksum,
            review_hash=plan.review_hash,
        )


def test_planner_rejects_invalid_names_sizes_and_multiple_statements() -> None:
    planner = MigrationPlanner()
    one = draft("CREATE TABLE app.items (id integer)", "DROP TABLE app.items")

    with pytest.raises(MigrationValidationError, match="name must not be empty"):
        planner.create_plan(name=" ", steps=[one])
    with pytest.raises(MigrationValidationError, match="may contain only"):
        planner.create_plan(name="bad name", steps=[one])
    with pytest.raises(MigrationValidationError, match="at least one"):
        planner.create_plan(name="empty", steps=[])
    with pytest.raises(MigrationValidationError, match=str(MAX_MIGRATION_STEPS)):
        planner.create_plan(name="too-many", steps=[one] * (MAX_MIGRATION_STEPS + 1))
    with pytest.raises(MigrationValidationError, match="exactly one SQL statement"):
        planner.create_plan(
            name="multiple",
            steps=[draft("CREATE TABLE app.a(id int); CREATE TABLE app.b(id int)", "DROP TABLE app.a")],
        )
    with pytest.raises(MigrationValidationError, match="rollback SQL must not be empty"):
        planner.create_plan(name="missing-rollback", steps=[draft("CREATE TABLE app.a(id int)", " ")])


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("CREATE TABLE app.items (id bigint)", MigrationBehavior.TRANSACTIONAL),
        ("ALTER TABLE app.items ADD COLUMN name text", MigrationBehavior.TRANSACTIONAL),
        ("GRANT SELECT ON TABLE app.items TO app_reader", MigrationBehavior.TRANSACTIONAL),
        ("CREATE INDEX CONCURRENTLY items_id_idx ON app.items (id)", MigrationBehavior.NON_TRANSACTIONAL),
        ("DROP INDEX CONCURRENTLY app.items_id_idx", MigrationBehavior.NON_TRANSACTIONAL),
        ("ALTER TABLE app.items DETACH PARTITION app.items_old CONCURRENTLY", MigrationBehavior.NON_TRANSACTIONAL),
        ("VACUUM app.items", MigrationBehavior.FORBIDDEN),
        ("ALTER SYSTEM SET work_mem = '64MB'", MigrationBehavior.FORBIDDEN),
        ("INSERT INTO app.items(id) VALUES (1)", MigrationBehavior.FORBIDDEN),
        ("CREATE TEMP TABLE scratch(id integer)", MigrationBehavior.FORBIDDEN),
        ("CREATE MATERIALIZED VIEW app.item_count AS SELECT count(*) FROM app.items", MigrationBehavior.FORBIDDEN),
        ("CREATE EXTENSION hstore", MigrationBehavior.EXTERNAL_SIDE_EFFECT),
        ("DROP SERVER app_server", MigrationBehavior.EXTERNAL_SIDE_EFFECT),
        ("ALTER SEQUENCE app.items_id_seq RESTART WITH 1", MigrationBehavior.EXTERNAL_SIDE_EFFECT),
        (
            "CREATE FUNCTION app.answer() RETURNS integer LANGUAGE sql SECURITY DEFINER AS $$ SELECT 42 $$",
            MigrationBehavior.EXTERNAL_SIDE_EFFECT,
        ),
    ],
)
def test_behavior_registry_is_conservative_and_explicit(sql: str, expected: MigrationBehavior) -> None:
    _kind, behavior, _warnings = classify_migration_sql(sql)
    assert behavior is expected


def test_plan_exposes_forward_and_reverse_operational_warnings() -> None:
    planner = MigrationPlanner()
    plan = planner.create_plan(
        name="risky-ddl",
        steps=[
            draft(
                "ALTER TABLE app.items ALTER COLUMN amount TYPE numeric(20,4)",
                "ALTER TABLE app.items ALTER COLUMN amount TYPE integer",
            ),
            draft("CREATE TABLE app.legacy (id integer)", "DROP TABLE app.legacy"),
            draft("GRANT SELECT ON TABLE app.items TO app_reader", "REVOKE SELECT ON TABLE app.items FROM app_reader"),
        ],
    )

    assert "access_exclusive_lock_possible" in plan.warnings
    assert "table_rewrite_possible" in plan.warnings
    assert "rollback:destructive_drop" in plan.warnings
    assert "privilege_change" in plan.warnings
    assert "rollback:privilege_change" in plan.warnings


def test_nontransactional_or_external_compensation_makes_plan_nonapplyable() -> None:
    planner = MigrationPlanner()
    plan = planner.create_plan(
        name="concurrent-index",
        steps=[
            draft(
                "CREATE INDEX CONCURRENTLY items_id_idx ON app.items (id)",
                "DROP INDEX CONCURRENTLY app.items_id_idx",
            )
        ],
    )

    assert plan.applyable is False
    assert plan.steps[0].behavior is MigrationBehavior.NON_TRANSACTIONAL
    assert plan.steps[0].rollback_behavior is MigrationBehavior.NON_TRANSACTIONAL
