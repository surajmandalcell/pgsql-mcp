"""Exhaustive validation-edge contracts for reviewed maintenance."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

from dataclasses import replace
from typing import Any
from typing import cast
from unittest.mock import AsyncMock

import pytest

from postgres_mcp.maintenance import MaintenanceOperation
from postgres_mcp.maintenance import MaintenanceOperationStatus
from postgres_mcp.maintenance import MaintenanceOptions
from postgres_mcp.maintenance import MaintenancePlan
from postgres_mcp.maintenance import MaintenancePlanner
from postgres_mcp.maintenance import MaintenanceRecord
from postgres_mcp.maintenance import MaintenanceRequest
from postgres_mcp.maintenance import MaintenanceService
from postgres_mcp.maintenance import MaintenanceTarget
from postgres_mcp.maintenance import MaintenanceValidationError
from postgres_mcp.maintenance import TargetSnapshot


def request(operation: MaintenanceOperation = MaintenanceOperation.VACUUM_ANALYZE) -> MaintenanceRequest:
    return MaintenanceRequest(
        name="nightly-items-maintenance",
        operation=operation,
        target=MaintenanceTarget("app", "items"),
    )


def snapshot(*, kind: str = "r") -> TargetSnapshot:
    return TargetSnapshot(
        oid=42,
        relation_kind=kind,
        persistence="p",
        is_partition=False,
        is_populated=True,
        has_usable_unique_index=kind == "m",
        is_exclusion_index=False,
    )


def reviewed_plan(operation: MaintenanceOperation = MaintenanceOperation.VACUUM_ANALYZE) -> MaintenancePlan:
    kind = "m" if operation is MaintenanceOperation.REFRESH_MATERIALIZED_VIEW_CONCURRENTLY else "r"
    return MaintenancePlanner().create_plan(request(operation), snapshot(kind=kind))


def test_identifier_name_options_and_request_type_edges() -> None:
    with pytest.raises(MaintenanceValidationError, match="schema must be a string"):
        MaintenanceTarget(cast(Any, 1), "items")
    with pytest.raises(MaintenanceValidationError, match="maintenance name must not contain NUL"):
        replace(request(), name="bad\x00name")
    with pytest.raises(MaintenanceValidationError, match="cannot exceed"):
        replace(request(), name="x" * 201)
    with pytest.raises(MaintenanceValidationError, match="skip_locked must be a boolean"):
        MaintenanceOptions(skip_locked=cast(Any, "yes"))
    with pytest.raises(MaintenanceValidationError, match="parallel must be an integer"):
        MaintenanceOptions(parallel=cast(Any, True))
    with pytest.raises(MaintenanceValidationError, match="unsupported maintenance operation"):
        replace(request(), operation=cast(Any, "unsupported"))
    with pytest.raises(MaintenanceValidationError, match="target must be"):
        replace(request(), target=cast(Any, object()))
    with pytest.raises(MaintenanceValidationError, match="options must be"):
        replace(request(), options=cast(Any, object()))


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"oid": 0}, "positive integer"),
        ({"relation_kind": "table"}, "one PostgreSQL relkind"),
        ({"persistence": "x"}, "must be p, u, or t"),
        ({"is_partition": cast(Any, 1)}, "flags must be boolean"),
    ],
)
def test_target_snapshot_type_edges(changes: dict[str, Any], message: str) -> None:
    with pytest.raises(MaintenanceValidationError, match=message):
        replace(snapshot(), **changes)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"target": cast(Any, object())}, "target must be"),
        ({"options": cast(Any, object())}, "options must be"),
        ({"target_oid": 0}, "positive integer"),
        ({"target_kind": "table"}, "one PostgreSQL relkind"),
        ({"target_persistence": "x"}, "must be p, u, or t"),
        ({"transaction_behavior": "transactional"}, "non_transactional"),
        ({"plan_version": 999}, "unsupported maintenance plan version"),
    ],
)
def test_plan_type_and_policy_edges(changes: dict[str, Any], message: str) -> None:
    with pytest.raises(MaintenanceValidationError, match=message):
        replace(reviewed_plan(), **changes)


def test_canonical_plan_rejects_wrong_shapes_and_missing_fields() -> None:
    plan = reviewed_plan()
    wrong_target = plan.canonical_payload()
    wrong_target["target"] = []
    with pytest.raises(MaintenanceValidationError, match="invalid canonical"):
        MaintenancePlan.from_canonical_payload(wrong_target, review_hash=plan.review_hash)

    missing = plan.canonical_payload()
    del missing["options"]
    with pytest.raises(MaintenanceValidationError, match="invalid canonical"):
        MaintenancePlan.from_canonical_payload(missing, review_hash=plan.review_hash)


def test_planner_rejects_invalid_boundaries_and_operation_options() -> None:
    planner = MaintenancePlanner()
    with pytest.raises(MaintenanceValidationError, match="request must be"):
        planner.create_plan(cast(Any, object()), snapshot())
    with pytest.raises(MaintenanceValidationError, match="snapshot must be"):
        planner.create_plan(request(), cast(Any, object()))
    with pytest.raises(MaintenanceValidationError, match="not supported for ANALYZE"):
        planner.create_plan(request(MaintenanceOperation.ANALYZE), snapshot(kind="i"))
    with pytest.raises(MaintenanceValidationError, match="only supported for VACUUM"):
        planner.create_plan(
            replace(
                request(MaintenanceOperation.ANALYZE),
                options=MaintenanceOptions(index_cleanup="off"),
            ),
            snapshot(),
        )
    with pytest.raises(MaintenanceValidationError, match="only supported for VACUUM ANALYZE or ANALYZE"):
        planner.create_plan(
            replace(
                request(MaintenanceOperation.REFRESH_MATERIALIZED_VIEW_CONCURRENTLY),
                options=MaintenanceOptions(skip_locked=True),
            ),
            snapshot(kind="m"),
        )


@pytest.mark.asyncio
async def test_service_rejects_noninteger_timeout_values_before_backend_access() -> None:
    backend = AsyncMock()
    service = MaintenanceService(backend)
    plan = reviewed_plan()

    with pytest.raises(ValueError, match="timeout_seconds must be an integer"):
        await service.apply(
            plan,
            review_hash=plan.review_hash,
            timeout_seconds=cast(Any, 1.5),
            lock_timeout_seconds=1,
        )
    with pytest.raises(ValueError, match="lock_timeout_seconds must be an integer"):
        await service.apply(
            plan,
            review_hash=plan.review_hash,
            timeout_seconds=30,
            lock_timeout_seconds=cast(Any, False),
        )
    backend.apply.assert_not_awaited()


def test_record_payload_is_redacted_and_structured() -> None:
    record = MaintenanceRecord(
        operation_id=7,
        name="maintenance",
        review_hash="a" * 64,
        plan_version=1,
        operation=MaintenanceOperation.ANALYZE,
        target=MaintenanceTarget("app", "items"),
        target_oid=42,
        status=MaintenanceOperationStatus.SUCCEEDED,
        started_at="started",
        finished_at="finished",
        error_code=None,
        applied_by="operator",
    )

    payload = record.to_payload()
    assert payload["operation_id"] == 7
    assert payload["target"] == {"schema": "app", "name": "items", "oid": 42}
    assert "applied_by" not in payload
