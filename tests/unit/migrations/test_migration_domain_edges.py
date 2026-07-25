"""Defensive ledger-decoding contracts for reviewed migration aggregates."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from typing import Any
from typing import Callable

import pytest

from postgres_mcp.migrations.domain import PLAN_VERSION
from postgres_mcp.migrations.domain import MigrationPlan
from postgres_mcp.migrations.domain import MigrationStepDraft
from postgres_mcp.migrations.domain import MigrationValidationError
from postgres_mcp.migrations.planner import MigrationPlanner


def valid_plan() -> MigrationPlan:
    return MigrationPlanner().create_plan(
        name="ledger-round-trip",
        steps=[MigrationStepDraft("CREATE TABLE app.items(id integer)", "DROP TABLE app.items")],
    )


def restore(payload: dict[str, Any], plan: MigrationPlan) -> MigrationPlan:
    return MigrationPlan.from_canonical_payload(
        payload,
        checksum=plan.checksum,
        review_hash=plan.review_hash,
    )


def test_integrity_rejects_malformed_hash_encodings() -> None:
    plan = valid_plan()

    with pytest.raises(MigrationValidationError, match="checksum must be"):
        replace(plan, checksum="not-a-digest").assert_integrity()
    with pytest.raises(MigrationValidationError, match="review_hash must be"):
        replace(plan, review_hash="not-a-digest").assert_integrity()


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda payload: payload.pop("warnings"), "invalid canonical shape"),
        (lambda payload: payload.__setitem__("plan_version", PLAN_VERSION + 1), "unsupported migration plan version"),
        (lambda payload: payload.__setitem__("name", 7), "migration name must be text"),
        (lambda payload: payload.__setitem__("steps", {}), "invalid steps or warnings"),
        (lambda payload: payload.__setitem__("warnings", {}), "invalid steps or warnings"),
        (lambda payload: payload["steps"].append({"position": 1}), "invalid canonical shape"),
        (lambda payload: payload["steps"][0].__setitem__("warnings", [1]), "invalid values"),
        (lambda payload: payload["steps"][0].__setitem__("rollback_warnings", [1]), "invalid values"),
        (lambda payload: payload["steps"][0].__setitem__("position", "not-an-integer"), "invalid values"),
        (lambda payload: payload["steps"][0].__setitem__("sql", 7), "invalid values"),
        (lambda payload: payload["steps"][0].__setitem__("behavior", "not-a-behavior"), "invalid values"),
        (lambda payload: payload["warnings"].append(7), "warnings must be text"),
    ],
)
def test_ledger_decoder_rejects_every_untrusted_shape(
    mutate: Callable[[dict[str, Any]], Any],
    message: str,
) -> None:
    plan = valid_plan()
    payload = deepcopy(plan.canonical_payload())
    mutate(payload)

    with pytest.raises(MigrationValidationError, match=message):
        restore(payload, plan)
