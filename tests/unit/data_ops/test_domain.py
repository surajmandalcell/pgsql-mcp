"""Domain contracts for structured, guarded PostgreSQL data operations."""

from __future__ import annotations

from dataclasses import replace
from typing import Any
from typing import cast

import pytest

from postgres_mcp.data_ops import ComparisonOperator
from postgres_mcp.data_ops import DataValidationError
from postgres_mcp.data_ops import DeleteRowsRequest
from postgres_mcp.data_ops import FilterCondition
from postgres_mcp.data_ops import FilterSet
from postgres_mcp.data_ops import InsertRowsRequest
from postgres_mcp.data_ops import MutationGuard
from postgres_mcp.data_ops import OrderDirection
from postgres_mcp.data_ops import OrderTerm
from postgres_mcp.data_ops import PageCursor
from postgres_mcp.data_ops import QualifiedRelation
from postgres_mcp.data_ops import SelectRowsRequest
from postgres_mcp.data_ops import UpdateRowsRequest
from postgres_mcp.data_ops import UpsertRowsRequest


def relation() -> QualifiedRelation:
    return QualifiedRelation(schema="app", name="accounts")


def test_qualified_relation_and_column_names_are_strict() -> None:
    assert relation().display_name == "app.accounts"

    for value in ("", " ", "bad\x00name"):
        with pytest.raises(DataValidationError):
            QualifiedRelation(schema=value, name="accounts")

    with pytest.raises(DataValidationError, match="cannot exceed"):
        QualifiedRelation(schema="a" * 64, name="accounts")


def test_filter_set_validates_operators_values_and_complexity() -> None:
    filters = FilterSet(
        all_of=(
            FilterCondition("tenant_id", ComparisonOperator.EQ, 7),
            FilterCondition("deleted_at", ComparisonOperator.IS_NULL),
        ),
        any_of=(
            FilterCondition("status", ComparisonOperator.IN, ("active", "trial")),
            FilterCondition("name", ComparisonOperator.ILIKE, "acme%"),
        ),
    )
    assert filters.term_count == 4

    with pytest.raises(DataValidationError, match="requires a value"):
        FilterCondition("id", ComparisonOperator.EQ)
    with pytest.raises(DataValidationError, match="does not accept a value"):
        FilterCondition("id", ComparisonOperator.IS_NULL, 1)
    with pytest.raises(DataValidationError, match="non-empty sequence"):
        FilterCondition("id", ComparisonOperator.IN, ())
    with pytest.raises(DataValidationError, match="maximum"):
        FilterSet(all_of=tuple(FilterCondition("id", ComparisonOperator.EQ, item) for item in range(65)))


def test_page_cursor_round_trip_is_relation_and_order_bound() -> None:
    order = (OrderTerm("created_at", OrderDirection.DESC), OrderTerm("id", OrderDirection.ASC))
    encoded = PageCursor.encode(relation(), order, ("2026-07-25T00:00:00+00:00", 42))
    decoded = PageCursor.decode(encoded, relation(), order)
    assert decoded.values == ("2026-07-25T00:00:00+00:00", 42)

    with pytest.raises(DataValidationError, match="different relation"):
        PageCursor.decode(encoded, QualifiedRelation("app", "other"), order)
    with pytest.raises(DataValidationError, match="ordering"):
        PageCursor.decode(encoded, relation(), (OrderTerm("id"),))
    with pytest.raises(DataValidationError, match="invalid page cursor"):
        PageCursor.decode(encoded[:-2] + "xx", relation(), order)


def test_page_cursor_rejects_oversized_or_non_finite_values() -> None:
    order = (OrderTerm("id"),)
    with pytest.raises(DataValidationError, match="invalid page cursor"):
        PageCursor.decode("x" * 20_000, relation(), order)
    with pytest.raises(DataValidationError, match="finite"):
        PageCursor.encode(relation(), order, (float("inf"),))
    with pytest.raises(DataValidationError, match="maximum encoded size"):
        PageCursor.encode(relation(), order, ("x" * 20_000,))


def test_select_request_is_bounded_and_requires_deterministic_order_for_cursor() -> None:
    request = SelectRowsRequest(
        relation=relation(),
        columns=("id", "email"),
        filters=FilterSet(),
        order_by=(OrderTerm("id"),),
        limit=25,
    )
    assert request.limit == 25

    with pytest.raises(DataValidationError, match="limit"):
        replace(request, limit=0)
    with pytest.raises(DataValidationError, match="duplicate"):
        replace(request, columns=("id", "id"))
    with pytest.raises(DataValidationError, match="order_by"):
        replace(request, cursor="opaque", order_by=())


def test_mutation_requests_require_explicit_guards_and_safe_shapes() -> None:
    guard = MutationGuard(max_affected_rows=2, expected_rows=1)
    assert guard.max_affected_rows == 2

    with pytest.raises(DataValidationError, match="cannot exceed"):
        MutationGuard(max_affected_rows=1, expected_rows=2)
    with pytest.raises(DataValidationError, match="integer"):
        MutationGuard(max_affected_rows=True)

    insert = InsertRowsRequest(
        relation=relation(),
        rows=({"email": "one@example.com"},),
        returning=("id",),
        guard=MutationGuard(max_affected_rows=1, expected_rows=1),
    )
    assert insert.rows[0]["email"] == "one@example.com"

    with pytest.raises(DataValidationError, match="submitted row count"):
        replace(insert, guard=MutationGuard(max_affected_rows=2, expected_rows=2))

    with pytest.raises(DataValidationError, match="same columns"):
        replace(insert, rows=({"email": "one@example.com"}, {"email": "two@example.com", "name": "Two"}))

    upsert = UpsertRowsRequest(
        relation=relation(),
        rows=({"email": "one@example.com", "name": "One"},),
        conflict_columns=("email",),
        update_columns=("name",),
        returning=("id",),
        guard=MutationGuard(max_affected_rows=1),
    )
    assert upsert.conflict_columns == ("email",)

    with pytest.raises(DataValidationError, match="conflict_columns"):
        replace(upsert, conflict_columns=())

    filters = FilterSet(all_of=(FilterCondition("id", ComparisonOperator.EQ, 1),))
    update = UpdateRowsRequest(
        relation=relation(),
        values={"name": "Updated"},
        filters=filters,
        concurrency=FilterSet(all_of=(FilterCondition("version", ComparisonOperator.EQ, 3),)),
        returning=("id", "version"),
        guard=MutationGuard(max_affected_rows=1, expected_rows=1),
    )
    assert update.values["name"] == "Updated"

    with pytest.raises(DataValidationError, match="filter"):
        replace(update, filters=FilterSet())
    with pytest.raises(DataValidationError, match="values"):
        replace(update, values={})

    delete = DeleteRowsRequest(
        relation=relation(),
        filters=filters,
        concurrency=FilterSet(),
        returning=("id",),
        guard=MutationGuard(max_affected_rows=1),
    )
    assert delete.filters.term_count == 1
    with pytest.raises(DataValidationError, match="filter"):
        replace(delete, filters=FilterSet())


def test_request_aggregate_rejects_untyped_collaborators() -> None:
    with pytest.raises(DataValidationError, match="QualifiedRelation"):
        SelectRowsRequest(
            relation=cast(Any, "app.accounts"),
            columns=(),
            filters=FilterSet(),
            order_by=(OrderTerm("id"),),
        )
    with pytest.raises(DataValidationError, match="FilterSet"):
        SelectRowsRequest(
            relation=relation(),
            columns=(),
            filters=cast(Any, ()),
            order_by=(OrderTerm("id"),),
        )
    with pytest.raises(DataValidationError, match="mapping"):
        InsertRowsRequest(
            relation=relation(),
            rows=cast(Any, ("not-a-row",)),
            returning=(),
            guard=MutationGuard(max_affected_rows=1),
        )
