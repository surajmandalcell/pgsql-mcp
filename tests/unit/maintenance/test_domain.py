"""Test-first contracts for reviewed nontransactional maintenance plans."""

from __future__ import annotations

from dataclasses import replace

import pytest

from postgres_mcp.maintenance import MaintenanceOperation
from postgres_mcp.maintenance import MaintenanceOptions
from postgres_mcp.maintenance import MaintenancePlanner
from postgres_mcp.maintenance import MaintenanceRequest
from postgres_mcp.maintenance import MaintenanceTarget
from postgres_mcp.maintenance import MaintenanceValidationError
from postgres_mcp.maintenance import TargetSnapshot


def target(name: str = "items") -> MaintenanceTarget:
    return MaintenanceTarget(schema="app", name=name)


def snapshot(*, kind: str = "r", unique_index: bool = False) -> TargetSnapshot:
    return TargetSnapshot(
        oid=42,
        relation_kind=kind,
        is_partition=False,
        has_usable_unique_index=unique_index,
    )


def request(operation: MaintenanceOperation = MaintenanceOperation.VACUUM_ANALYZE) -> MaintenanceRequest:
    return MaintenanceRequest(
        name="nightly-items-maintenance",
        operation=operation,
        target=target(),
    )


def test_plan_hash_is_deterministic_and_binds_live_target_identity() -> None:
    planner = MaintenancePlanner()
    first = planner.create_plan(request(), snapshot())
    repeated = planner.create_plan(request(), snapshot())
    replaced = planner.create_plan(request(), replace(snapshot(), oid=43))

    assert first.review_hash == repeated.review_hash
    assert first.review_hash != replaced.review_hash
    assert len(first.review_hash) == 64
    first.assert_integrity()

    with pytest.raises(MaintenanceValidationError, match="integrity"):
        replace(first, target_oid=99).assert_integrity()


def test_identifiers_and_stable_names_are_strict() -> None:
    with pytest.raises(MaintenanceValidationError, match="schema"):
        MaintenanceTarget(schema="", name="items")
    with pytest.raises(MaintenanceValidationError, match="NUL"):
        MaintenanceTarget(schema="app", name="bad\x00name")
    with pytest.raises(MaintenanceValidationError, match="name"):
        replace(request(), name=" ")
    with pytest.raises(MaintenanceValidationError, match="63"):
        MaintenanceTarget(schema="app", name="x" * 64)


def test_operation_specific_options_are_bounded_and_conservative() -> None:
    options = MaintenanceOptions(skip_locked=True, index_cleanup="off", parallel=2)
    plan = MaintenancePlanner().create_plan(replace(request(), options=options), snapshot())

    assert plan.options == options
    assert "cannot_roll_back" in plan.warnings
    assert plan.transaction_behavior == "non_transactional"

    with pytest.raises(MaintenanceValidationError, match="index_cleanup"):
        MaintenanceOptions(index_cleanup="invalid")
    with pytest.raises(MaintenanceValidationError, match="parallel"):
        MaintenanceOptions(parallel=1025)
    with pytest.raises(MaintenanceValidationError, match="only supported"):
        MaintenancePlanner().create_plan(
            replace(
                request(MaintenanceOperation.REINDEX_INDEX_CONCURRENTLY),
                options=MaintenanceOptions(skip_locked=True),
            ),
            snapshot(kind="i"),
        )


@pytest.mark.parametrize("kind", ["r", "p", "m"])
def test_vacuum_analyze_accepts_supported_relation_classes(kind: str) -> None:
    plan = MaintenancePlanner().create_plan(request(), snapshot(kind=kind))
    assert plan.target_kind == kind


def test_analyze_allows_foreign_tables_but_vacuum_does_not() -> None:
    analyze = MaintenancePlanner().create_plan(
        request(MaintenanceOperation.ANALYZE),
        snapshot(kind="f"),
    )
    assert analyze.operation is MaintenanceOperation.ANALYZE

    with pytest.raises(MaintenanceValidationError, match="not supported"):
        MaintenancePlanner().create_plan(request(), snapshot(kind="f"))


def test_reindex_requires_an_index_target() -> None:
    plan = MaintenancePlanner().create_plan(
        request(MaintenanceOperation.REINDEX_INDEX_CONCURRENTLY),
        snapshot(kind="i"),
    )
    assert plan.target_kind == "i"

    with pytest.raises(MaintenanceValidationError, match="index"):
        MaintenancePlanner().create_plan(
            request(MaintenanceOperation.REINDEX_INDEX_CONCURRENTLY),
            snapshot(kind="r"),
        )


def test_concurrent_refresh_requires_materialized_view_and_usable_unique_index() -> None:
    plan = MaintenancePlanner().create_plan(
        request(MaintenanceOperation.REFRESH_MATERIALIZED_VIEW_CONCURRENTLY),
        snapshot(kind="m", unique_index=True),
    )
    assert plan.preconditions["has_usable_unique_index"] is True

    with pytest.raises(MaintenanceValidationError, match="materialized view"):
        MaintenancePlanner().create_plan(
            request(MaintenanceOperation.REFRESH_MATERIALIZED_VIEW_CONCURRENTLY),
            snapshot(kind="r", unique_index=True),
        )
    with pytest.raises(MaintenanceValidationError, match="unique index"):
        MaintenancePlanner().create_plan(
            request(MaintenanceOperation.REFRESH_MATERIALIZED_VIEW_CONCURRENTLY),
            snapshot(kind="m", unique_index=False),
        )


def test_plan_payload_contains_no_raw_sql_escape_hatch() -> None:
    payload = MaintenancePlanner().create_plan(request(), snapshot()).to_payload()

    assert payload["operation"] == "vacuum_analyze"
    assert payload["target"] == {"schema": "app", "name": "items", "oid": 42, "kind": "r"}
    assert "sql" not in str(payload).lower()
