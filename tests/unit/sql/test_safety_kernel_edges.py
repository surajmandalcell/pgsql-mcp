"""Focused branch contracts for the public SQL safety kernel."""

from __future__ import annotations

from typing import Any

import pytest
from pglast.ast import DeleteStmt
from pglast.ast import InsertStmt
from pglast.ast import UpdateStmt

from postgres_mcp.sql import query_guard
from postgres_mcp.sql import transaction
from postgres_mcp.sql.transaction import TransactionStep
from postgres_mcp.sql.transaction import TransactionValidationError
from postgres_mcp.sql.transaction import sql_for_validation
from postgres_mcp.sql.transaction import validate_transaction_steps


def test_placeholder_scanner_preserves_doubled_quotes_and_lonely_dollar_prefix() -> None:
    rendered = sql_for_validation(
        "SELECT 'it''s %s', \"quoted\"\"%s\", $not_a_delimiter, %s",
        parameter_count=1,
    )

    assert "'it''s %s'" in rendered
    assert '"quoted""%s"' in rendered
    assert "$not_a_delimiter" in rendered
    assert rendered.endswith("NULL")


def test_transaction_ast_traversal_skips_missing_slots(monkeypatch: pytest.MonkeyPatch) -> None:
    class DefensiveNode:
        __slots__ = ("missing", "children")

        def __init__(self) -> None:
            self.children: tuple[Any, ...] = ()

    monkeypatch.setattr(transaction, "Node", DefensiveNode)

    assert transaction._contains_nested_mutation(  # pyright: ignore[reportPrivateUsage]
        DefensiveNode(),
        root=object(),
    ) is False


def test_query_guard_ast_traversal_skips_missing_slots(monkeypatch: pytest.MonkeyPatch) -> None:
    class DefensiveNode:
        __slots__ = ("missing", "children")

        def __init__(self) -> None:
            self.children: tuple[Any, ...] = ()

    monkeypatch.setattr(query_guard, "Node", DefensiveNode)

    query_guard._reject_session_mutation(DefensiveNode())  # pyright: ignore[reportPrivateUsage]


def test_transaction_policy_remains_safe_when_merge_ast_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(transaction, "MergeStmt", None)

    assert transaction._mutating_types() == (InsertStmt, UpdateStmt, DeleteStmt)  # pyright: ignore[reportPrivateUsage]
    validated = validate_transaction_steps(
        [TransactionStep(sql="SELECT 1")],
        read_only=True,
        absolute_max_rows=100,
    )
    assert validated[0].statement_kind == "select"


def test_data_modifying_cte_is_rejected_by_recursive_policy() -> None:
    with pytest.raises(TransactionValidationError, match="data-modifying CTE"):
        validate_transaction_steps(
            [
                TransactionStep(
                    sql="WITH deleted AS (DELETE FROM public.items WHERE id = 1 RETURNING id) SELECT * FROM deleted",
                )
            ],
            read_only=False,
            absolute_max_rows=100,
        )
