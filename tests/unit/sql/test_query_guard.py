"""Tests for single-statement read-only query protection."""

import time
from unittest.mock import AsyncMock
from unittest.mock import patch

import pytest

from postgres_mcp.sql.query_guard import MAX_SQL_CHARACTERS
from postgres_mcp.sql.query_guard import SafeQueryExecutor
from postgres_mcp.sql.results import BoundedQueryResult
from postgres_mcp.sql.sql_driver import SqlDriver
from postgres_mcp.sql.transaction import TransactionValidationError
from postgres_mcp.sql.transaction import parse_single_statement
from postgres_mcp.sql.transaction import sql_for_validation


def test_placeholder_scanner_preserves_quoted_regions_and_comments() -> None:
    sql = """
    SELECT %s, '%s', "quoted%s", $$body %s$$, $tag$body %s$tag$, %s
    -- ignored %s
    /* outer %s /* nested %s */ still ignored */
    """
    rendered = sql_for_validation(sql, parameter_count=2)
    assert "SELECT NULL" in rendered
    assert "'%s'" in rendered
    assert '"quoted%s"' in rendered
    assert "$$body %s$$" in rendered
    assert "$tag$body %s$tag$" in rendered
    assert rendered.count("NULL") == 2


def test_placeholder_scanner_handles_escape_strings_and_literal_percent() -> None:
    rendered = sql_for_validation(r"SELECT E'it\'s %s', 10 %% 3, %b, %t", parameter_count=2)
    assert "E'it\'s %s'" in rendered
    assert "10 % 3" in rendered
    assert rendered.endswith("NULL, NULL")


def test_placeholder_scanner_preserves_backslashes_in_ordinary_strings() -> None:
    sql = r"SELECT 'value\' || %s"
    rendered = sql_for_validation(sql, parameter_count=1)
    assert rendered == r"SELECT 'value\' || NULL"
    assert type(parse_single_statement(sql, parameter_count=1)).__name__ == "SelectStmt"


@pytest.mark.parametrize(
    ("sql", "message"),
    [
        ("SELECT %(value)s", "named SQL parameters"),
        ("SELECT '%s", "unterminated quoted SQL region"),
        ('SELECT "name', "unterminated quoted SQL region"),
        ("SELECT /* comment", "unterminated quoted SQL region"),
        ("SELECT $$body", "unterminated quoted SQL region"),
        ("SELECT E'escaped", "unterminated quoted SQL region"),
    ],
)
def test_placeholder_scanner_rejects_unsupported_or_unterminated_input(sql: str, message: str) -> None:
    with pytest.raises(TransactionValidationError, match=message):
        sql_for_validation(sql, parameter_count=0)


def test_placeholder_count_must_match_parameters() -> None:
    with pytest.raises(TransactionValidationError, match="contains 2 positional placeholders but 1 parameters"):
        sql_for_validation("SELECT %s, %s", parameter_count=1)


def test_parse_single_statement_supports_native_parameters() -> None:
    statement = parse_single_statement("SELECT %s::integer", parameter_count=1)
    assert type(statement).__name__ == "SelectStmt"


@pytest.mark.asyncio
async def test_safe_query_executor_passes_original_sql_and_parameters() -> None:
    base_driver = AsyncMock(spec=SqlDriver)
    expected = BoundedQueryResult(
        rows=[{"value": 1}],
        columns=[],
        row_count=1,
        truncated=False,
        affected_rows=None,
        command="SELECT",
    )
    base_driver.execute_bounded_query.return_value = expected
    executor = SafeQueryExecutor(base_driver, timeout_seconds=2)

    result = await executor.execute_bounded_query(
        "SELECT %s::integer AS value",
        params=[1],
        max_rows=10,
    )

    assert result is expected
    base_driver.execute_bounded_query.assert_awaited_once_with(
        "SELECT %s::integer AS value",
        params=[1],
        max_rows=10,
        force_readonly=True,
        timeout_seconds=2,
    )


@pytest.mark.asyncio
async def test_safe_query_executor_rejects_writes_and_multiple_statements() -> None:
    base_driver = AsyncMock(spec=SqlDriver)
    executor = SafeQueryExecutor(base_driver, timeout_seconds=2)

    with pytest.raises(TransactionValidationError, match="statement type 'delete'"):
        await executor.execute_bounded_query("DELETE FROM items WHERE id = 1", params=None, max_rows=10)
    with pytest.raises(TransactionValidationError, match="exactly one"):
        await executor.execute_bounded_query("SELECT 1; SELECT 2", params=None, max_rows=10)

    base_driver.execute_bounded_query.assert_not_awaited()


@pytest.mark.asyncio
async def test_safe_query_executor_rejects_admin_statements_and_session_mutation() -> None:
    base_driver = AsyncMock(spec=SqlDriver)
    executor = SafeQueryExecutor(base_driver, timeout_seconds=2)

    with pytest.raises(TransactionValidationError, match="statement type 'createextension'"):
        await executor.execute_bounded_query("CREATE EXTENSION hstore", params=None, max_rows=10)
    with pytest.raises(TransactionValidationError, match="hypopg_reset"):
        await executor.execute_bounded_query("SELECT hypopg_reset()", params=None, max_rows=10)

    base_driver.execute_bounded_query.assert_not_awaited()


@pytest.mark.asyncio
async def test_safe_query_executor_reports_database_timeout() -> None:
    base_driver = AsyncMock(spec=SqlDriver)
    base_driver.execute_bounded_query.side_effect = TimeoutError
    executor = SafeQueryExecutor(base_driver, timeout_seconds=3)

    with pytest.raises(ValueError, match="validation or execution timed out after 3 seconds"):
        await executor.execute_bounded_query("SELECT 1", params=None, max_rows=10)


@pytest.mark.asyncio
async def test_safe_query_executor_times_out_synchronous_validation() -> None:
    base_driver = AsyncMock(spec=SqlDriver)
    executor = SafeQueryExecutor(base_driver, timeout_seconds=0.01)

    def slow_validation(_query: str, *, parameter_count: int) -> None:
        assert parameter_count == 0
        time.sleep(0.1)

    with patch.object(executor.validator, "validate_query", side_effect=slow_validation):
        with pytest.raises(ValueError, match="validation or execution timed out"):
            await executor.execute_bounded_query("SELECT 1", params=None, max_rows=10)

    base_driver.execute_bounded_query.assert_not_awaited()


@pytest.mark.asyncio
async def test_safe_query_validator_has_a_bounded_public_entrypoint() -> None:
    base_driver = AsyncMock(spec=SqlDriver)
    executor = SafeQueryExecutor(base_driver, timeout_seconds=0.01)

    with patch.object(executor.validator, "validate_query", side_effect=lambda *_args, **_kwargs: time.sleep(0.1)):
        with pytest.raises(ValueError, match="query validation timed out"):
            await executor.validate_query("SELECT 1")


@pytest.mark.asyncio
async def test_safe_query_executor_rejects_oversized_sql_before_parsing() -> None:
    base_driver = AsyncMock(spec=SqlDriver)
    executor = SafeQueryExecutor(base_driver, timeout_seconds=2)
    oversized = "SELECT '" + ("x" * MAX_SQL_CHARACTERS) + "'"

    with pytest.raises(ValueError, match=f"cannot exceed {MAX_SQL_CHARACTERS}"):
        await executor.execute_bounded_query(oversized, params=None, max_rows=10)

    base_driver.execute_bounded_query.assert_not_awaited()


def test_safe_query_executor_requires_positive_timeout() -> None:
    with pytest.raises(ValueError, match="greater than zero"):
        SafeQueryExecutor(AsyncMock(spec=SqlDriver), timeout_seconds=0)
