"""Tests for atomic transaction request validation."""

from unittest.mock import Mock

import pytest

from postgres_mcp.sql.results import ColumnInfo
from postgres_mcp.sql.transaction import IsolationLevel
from postgres_mcp.sql.transaction import ResultMode
from postgres_mcp.sql.transaction import TransactionExecutionError
from postgres_mcp.sql.transaction import TransactionExecutionResult
from postgres_mcp.sql.transaction import TransactionStep
from postgres_mcp.sql.transaction import TransactionStepResult
from postgres_mcp.sql.transaction import TransactionValidationError
from postgres_mcp.sql.transaction import build_begin_statement
from postgres_mcp.sql.transaction import parse_single_statement
from postgres_mcp.sql.transaction import validate_transaction_steps


def step(sql: str, **overrides: object) -> TransactionStep:
    values = {
        "sql": sql,
        "params": (),
        "expected_rows": None,
        "max_affected_rows": None,
        "result_mode": ResultMode.SUMMARY,
        "max_rows": 100,
    }
    values.update(overrides)
    return TransactionStep(**values)  # type: ignore[arg-type]


def test_isolation_sql() -> None:
    assert IsolationLevel.READ_COMMITTED.sql == "READ COMMITTED"
    assert IsolationLevel.REPEATABLE_READ.sql == "REPEATABLE READ"
    assert IsolationLevel.SERIALIZABLE.sql == "SERIALIZABLE"


def test_begin_statement_builder_is_fully_allowlisted() -> None:
    assert build_begin_statement(IsolationLevel.READ_COMMITTED, read_only=True) == ("BEGIN ISOLATION LEVEL READ COMMITTED READ ONLY")
    assert build_begin_statement(IsolationLevel.REPEATABLE_READ, read_only=False) == ("BEGIN ISOLATION LEVEL REPEATABLE READ READ WRITE")
    assert build_begin_statement(IsolationLevel.SERIALIZABLE, read_only=True) == ("BEGIN ISOLATION LEVEL SERIALIZABLE READ ONLY")


@pytest.mark.parametrize("sql", ["", "   "])
def test_parse_rejects_empty_sql(sql: str) -> None:
    with pytest.raises(TransactionValidationError, match="must not be empty"):
        parse_single_statement(sql)


def test_parse_rejects_invalid_and_multiple_statements() -> None:
    with pytest.raises(TransactionValidationError, match="failed to parse"):
        parse_single_statement("SELECT FROM")
    with pytest.raises(TransactionValidationError, match="exactly one"):
        parse_single_statement("SELECT 1; SELECT 2")


def test_validate_select_and_result_payloads() -> None:
    validated = validate_transaction_steps([step("SELECT 1")], read_only=True, absolute_max_rows=100)
    assert validated[0].statement_kind == "select"
    assert validated[0].mutating is False

    result = TransactionExecutionResult(
        committed=True,
        isolation=IsolationLevel.READ_COMMITTED,
        read_only=True,
        steps=[
            TransactionStepResult(
                index=0,
                statement_kind="select",
                affected_rows=None,
                columns=[ColumnInfo("value", 23)],
                rows=[{"value": 1}],
                truncated=False,
            )
        ],
    )
    payload = result.to_payload()
    assert payload["committed"] is True
    assert payload["steps"][0]["row_count"] == 1


def test_validate_requires_steps_and_valid_row_limits() -> None:
    with pytest.raises(TransactionValidationError, match="at least one"):
        validate_transaction_steps([], read_only=False, absolute_max_rows=100)
    with pytest.raises(TransactionValidationError, match="between 1 and 100"):
        validate_transaction_steps([step("SELECT 1", max_rows=0)], read_only=False, absolute_max_rows=100)
    with pytest.raises(TransactionValidationError, match="between 1 and 100"):
        validate_transaction_steps([step("SELECT 1", max_rows=101)], read_only=False, absolute_max_rows=100)


def test_validate_expected_row_constraints() -> None:
    with pytest.raises(TransactionValidationError, match="cannot be negative"):
        validate_transaction_steps([step("SELECT 1", expected_rows=-1)], read_only=False, absolute_max_rows=100)
    with pytest.raises(TransactionValidationError, match="cannot exceed"):
        validate_transaction_steps(
            [step("UPDATE t SET value = 1 WHERE id = 1", expected_rows=2, max_affected_rows=1)],
            read_only=False,
            absolute_max_rows=100,
        )


@pytest.mark.parametrize(
    ("sql", "message"),
    [
        ("CREATE TABLE t(id integer)", "not supported"),
        ("SELECT 1 INTO created_table", "SELECT INTO"),
        ("SELECT * FROM t FOR UPDATE", "locking SELECT"),
        ("UPDATE t SET value = 1", "requires a WHERE"),
        ("DELETE FROM t", "requires a WHERE"),
    ],
)
def test_validate_rejects_unsafe_statement_shapes(sql: str, message: str) -> None:
    with pytest.raises(TransactionValidationError, match=message):
        validate_transaction_steps(
            [step(sql, max_affected_rows=1)],
            read_only=False,
            absolute_max_rows=100,
        )


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO t(id) VALUES (1)",
        "UPDATE t SET value = 1 WHERE id = 1",
        "DELETE FROM t WHERE id = 1",
    ],
)
def test_validate_write_guards(sql: str) -> None:
    with pytest.raises(TransactionValidationError, match="requires max_affected_rows"):
        validate_transaction_steps([step(sql)], read_only=False, absolute_max_rows=100)
    with pytest.raises(TransactionValidationError, match="greater than zero"):
        validate_transaction_steps(
            [step(sql, max_affected_rows=0)],
            read_only=False,
            absolute_max_rows=100,
        )
    validated = validate_transaction_steps(
        [step(sql, max_affected_rows=1)],
        read_only=False,
        absolute_max_rows=100,
    )
    assert validated[0].mutating is True


def test_read_only_rejects_mutation() -> None:
    with pytest.raises(TransactionValidationError, match="read-only"):
        validate_transaction_steps(
            [step("INSERT INTO t(id) VALUES (1)", max_affected_rows=1)],
            read_only=True,
            absolute_max_rows=100,
        )


def test_execution_error_payload() -> None:
    error = TransactionExecutionError("boom", failed_step=2)
    assert error.rolled_back is True
    assert error.to_payload() == {
        "committed": False,
        "rolled_back": True,
        "failed_step": 2,
        "error": "boom",
    }


def test_parse_returns_inner_statement() -> None:
    statement = parse_single_statement("SELECT 1")
    assert type(statement).__name__ == "SelectStmt"


def test_non_raw_statement_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    marker = Mock()
    monkeypatch.setattr("postgres_mcp.sql.transaction.pglast.parse_sql", lambda _sql: (marker,))
    assert parse_single_statement("SELECT 1") is marker


def test_validate_parameterized_transaction_step() -> None:
    validated = validate_transaction_steps(
        [
            step(
                "UPDATE public.items SET value = %s WHERE id = %s",
                params=("updated", 1),
                expected_rows=1,
                max_affected_rows=1,
            )
        ],
        read_only=False,
        absolute_max_rows=100,
    )
    assert validated[0].statement_kind == "update"


def test_validate_parameter_count_and_named_parameters() -> None:
    with pytest.raises(TransactionValidationError, match="contains 2 positional placeholders but 1 parameters"):
        validate_transaction_steps(
            [step("SELECT %s, %s", params=(1,))],
            read_only=True,
            absolute_max_rows=100,
        )
    with pytest.raises(TransactionValidationError, match="named SQL parameters"):
        validate_transaction_steps(
            [step("SELECT %(value)s", params=(1,))],
            read_only=True,
            absolute_max_rows=100,
        )


def test_validate_rejects_data_modifying_cte() -> None:
    sql = """
    WITH changed AS (
        UPDATE public.items SET value = 1 WHERE id = 1 RETURNING id
    )
    SELECT id FROM changed
    """
    with pytest.raises(TransactionValidationError, match="data-modifying CTE"):
        validate_transaction_steps(
            [step(sql, max_affected_rows=1)],
            read_only=False,
            absolute_max_rows=100,
        )


def test_validate_rejects_mutation_guards_on_select() -> None:
    with pytest.raises(TransactionValidationError, match="expected_rows is supported only"):
        validate_transaction_steps(
            [step("SELECT 1", expected_rows=1)],
            read_only=True,
            absolute_max_rows=100,
        )
    with pytest.raises(TransactionValidationError, match="max_affected_rows is supported only"):
        validate_transaction_steps(
            [step("SELECT 1", max_affected_rows=1)],
            read_only=True,
            absolute_max_rows=100,
        )


def test_validate_requires_positive_absolute_row_limit() -> None:
    with pytest.raises(TransactionValidationError, match="absolute_max_rows"):
        validate_transaction_steps(
            [step("SELECT 1")],
            read_only=True,
            absolute_max_rows=0,
        )
