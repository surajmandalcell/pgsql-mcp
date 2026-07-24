"""Validation and result models for atomic multi-step SQL transactions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

import pglast
from pglast.ast import DeleteStmt
from pglast.ast import InsertStmt
from pglast.ast import Node
from pglast.ast import RawStmt
from pglast.ast import SelectStmt
from pglast.ast import UpdateStmt

from .results import ColumnInfo
from .results import encode_postgres_value

try:
    from pglast.ast import MergeStmt
except ImportError:  # pragma: no cover - only relevant to old pglast releases
    MergeStmt = None  # type: ignore[misc,assignment]


class IsolationLevel(str, Enum):
    """PostgreSQL transaction isolation levels supported by the public API."""

    READ_COMMITTED = "read committed"
    REPEATABLE_READ = "repeatable read"
    SERIALIZABLE = "serializable"

    @property
    def sql(self) -> str:
        """Return the trusted SQL spelling used in a BEGIN statement."""
        return self.value.upper()


class ResultMode(str, Enum):
    """Controls how much data a transaction step returns."""

    NONE = "none"
    SUMMARY = "summary"
    ROWS = "rows"


@dataclass(frozen=True, slots=True)
class TransactionStep:
    """One caller-supplied statement in an atomic transaction."""

    sql: str
    params: tuple[Any, ...] = ()
    expected_rows: int | None = None
    max_affected_rows: int | None = None
    result_mode: ResultMode = ResultMode.SUMMARY
    max_rows: int = 100


@dataclass(frozen=True, slots=True)
class ValidatedTransactionStep:
    """A statement that has passed all pre-execution safety checks."""

    step: TransactionStep
    statement_kind: str
    mutating: bool


@dataclass(frozen=True, slots=True)
class TransactionStepResult:
    """Bounded result metadata for one committed transaction step."""

    index: int
    statement_kind: str
    affected_rows: int | None
    columns: list[ColumnInfo]
    rows: list[dict[str, Any]]
    truncated: bool

    def to_payload(self) -> dict[str, Any]:
        """Return a stable MCP response payload."""
        return {
            "index": self.index,
            "statement_kind": self.statement_kind,
            "affected_rows": self.affected_rows,
            "columns": [column.to_payload() for column in self.columns],
            "rows": encode_postgres_value(self.rows),
            "row_count": len(self.rows),
            "truncated": self.truncated,
        }


@dataclass(frozen=True, slots=True)
class TransactionExecutionResult:
    """The result of a transaction whose commit completed successfully."""

    committed: bool
    isolation: IsolationLevel
    read_only: bool
    steps: list[TransactionStepResult]

    def to_payload(self) -> dict[str, Any]:
        """Return a stable MCP response payload."""
        return {
            "committed": self.committed,
            "isolation": self.isolation.value,
            "read_only": self.read_only,
            "steps": [step.to_payload() for step in self.steps],
        }


class TransactionValidationError(ValueError):
    """A transaction request is unsafe before any database work begins."""


class TransactionExecutionError(RuntimeError):
    """A transaction failed and was rolled back."""

    def __init__(self, message: str, *, failed_step: int | None = None):
        super().__init__(message)
        self.failed_step = failed_step
        self.rolled_back = True

    def to_payload(self) -> dict[str, Any]:
        """Return a response that makes rollback explicit to an MCP client."""
        return {
            "committed": False,
            "rolled_back": self.rolled_back,
            "failed_step": self.failed_step,
            "error": str(self),
        }


def sql_for_validation(sql: str, *, parameter_count: int | None = None) -> str:
    """Replace psycopg positional placeholders outside quoted SQL regions.

    PostgreSQL's parser does not recognize psycopg's ``%s``, ``%b``, and ``%t``
    client placeholders. This scanner substitutes only real placeholders with
    ``NULL`` while preserving strings, quoted identifiers, comments, and
    dollar-quoted bodies. When ``parameter_count`` is supplied, a mismatch is
    rejected before a database connection is checked out.
    """
    output: list[str] = []
    placeholder_count = 0
    index = 0
    state = "normal"
    block_comment_depth = 0
    dollar_delimiter: str | None = None

    while index < len(sql):
        current = sql[index]
        following = sql[index + 1] if index + 1 < len(sql) else ""

        if state == "single_quote":
            output.append(current)
            if current == "\\" and following:
                output.append(following)
                index += 2
                continue
            if current == "'":
                if following == "'":
                    output.append(following)
                    index += 2
                    continue
                state = "normal"
            index += 1
            continue

        if state == "double_quote":
            output.append(current)
            if current == '"':
                if following == '"':
                    output.append(following)
                    index += 2
                    continue
                state = "normal"
            index += 1
            continue

        if state == "line_comment":
            output.append(current)
            if current in "\r\n":
                state = "normal"
            index += 1
            continue

        if state == "block_comment":
            if current == "/" and following == "*":
                output.extend((current, following))
                block_comment_depth += 1
                index += 2
                continue
            if current == "*" and following == "/":
                output.extend((current, following))
                block_comment_depth -= 1
                index += 2
                if block_comment_depth == 0:
                    state = "normal"
                continue
            output.append(current)
            index += 1
            continue

        if state == "dollar_quote":
            assert dollar_delimiter is not None
            if sql.startswith(dollar_delimiter, index):
                output.append(dollar_delimiter)
                index += len(dollar_delimiter)
                dollar_delimiter = None
                state = "normal"
            else:
                output.append(current)
                index += 1
            continue

        if current == "'":
            output.append(current)
            state = "single_quote"
            index += 1
            continue
        if current == '"':
            output.append(current)
            state = "double_quote"
            index += 1
            continue
        if current == "-" and following == "-":
            output.extend((current, following))
            state = "line_comment"
            index += 2
            continue
        if current == "/" and following == "*":
            output.extend((current, following))
            state = "block_comment"
            block_comment_depth = 1
            index += 2
            continue
        if current == "$":
            delimiter_end = index + 1
            while delimiter_end < len(sql) and (sql[delimiter_end].isalnum() or sql[delimiter_end] == "_"):
                delimiter_end += 1
            if delimiter_end < len(sql) and sql[delimiter_end] == "$":
                dollar_delimiter = sql[index : delimiter_end + 1]
                output.append(dollar_delimiter)
                index = delimiter_end + 1
                state = "dollar_quote"
                continue
        if current == "%" and following == "%":
            # Psycopg turns %% into one literal percent before PostgreSQL sees it.
            output.append("%")
            index += 2
            continue
        if current == "%" and following in {"s", "b", "t"}:
            output.append("NULL")
            placeholder_count += 1
            index += 2
            continue
        if current == "%" and following == "(":
            raise TransactionValidationError("named SQL parameters are not supported; use positional placeholders")

        output.append(current)
        index += 1

    if state in {"single_quote", "double_quote", "block_comment", "dollar_quote"}:
        # pglast would reject these as well, but this gives a deterministic error
        # before placeholder-count validation can obscure the syntax failure.
        raise TransactionValidationError("unterminated quoted SQL region")
    if parameter_count is not None and placeholder_count != parameter_count:
        raise TransactionValidationError(
            f"SQL contains {placeholder_count} positional placeholders but {parameter_count} parameters were provided"
        )
    return "".join(output)


def parse_single_statement(sql: str, *, parameter_count: int | None = None) -> Any:
    """Parse and return exactly one PostgreSQL statement."""
    if not sql or not sql.strip():
        raise TransactionValidationError("SQL must not be empty")
    validation_sql = sql_for_validation(sql, parameter_count=parameter_count)
    try:
        parsed = pglast.parse_sql(validation_sql)
    except pglast.parser.ParseError as exc:
        raise TransactionValidationError("failed to parse SQL statement") from exc
    if len(parsed) != 1:
        raise TransactionValidationError("exactly one SQL statement is required")
    statement = parsed[0]
    return statement.stmt if isinstance(statement, RawStmt) else statement


def _statement_kind(statement: Any) -> str:
    name = type(statement).__name__
    return name.removesuffix("Stmt").lower()


def _mutating_types() -> tuple[type[Any], ...]:
    types: tuple[type[Any], ...] = (InsertStmt, UpdateStmt, DeleteStmt)
    if MergeStmt is not None:
        types += (MergeStmt,)
    return types


def _is_mutating(statement: Any) -> bool:
    return isinstance(statement, _mutating_types())


def _contains_nested_mutation(node: Any, *, root: Any) -> bool:
    """Return whether an AST contains a mutation below its root node."""
    if node is not root and _is_mutating(node):
        return True
    if isinstance(node, Node):
        for attribute_name in node.__slots__:
            if attribute_name.startswith("_"):
                continue
            try:
                value = getattr(node, attribute_name)
            except AttributeError:
                continue
            if _contains_nested_mutation(value, root=root):
                return True
        return False
    if isinstance(node, (list, tuple)):
        return any(_contains_nested_mutation(item, root=root) for item in node)
    return False


def _validate_statement_policy(statement: Any, step: TransactionStep, *, read_only: bool) -> tuple[str, bool]:
    kind = _statement_kind(statement)
    mutating = _is_mutating(statement)

    allowed_types: tuple[type[Any], ...] = (SelectStmt, InsertStmt, UpdateStmt, DeleteStmt)
    if MergeStmt is not None:
        allowed_types += (MergeStmt,)
    if not isinstance(statement, allowed_types):
        raise TransactionValidationError(f"statement type '{kind}' is not supported in atomic transactions")
    if isinstance(statement, SelectStmt) and _contains_nested_mutation(statement, root=statement):
        raise TransactionValidationError("data-modifying CTEs are not supported in atomic transactions")
    if read_only and mutating:
        raise TransactionValidationError(f"statement type '{kind}' is not allowed in a read-only transaction")

    if isinstance(statement, SelectStmt):
        if getattr(statement, "intoClause", None):
            raise TransactionValidationError("SELECT INTO is not allowed in atomic transactions")
        if getattr(statement, "lockingClause", None):
            raise TransactionValidationError("locking SELECT clauses are not allowed in atomic transactions")
        if step.expected_rows is not None:
            raise TransactionValidationError("expected_rows is supported only for mutating statements")
        if step.max_affected_rows is not None:
            raise TransactionValidationError("max_affected_rows is supported only for mutating statements")

    if isinstance(statement, (UpdateStmt, DeleteStmt)) and getattr(statement, "whereClause", None) is None:
        raise TransactionValidationError(f"{kind.upper()} requires a WHERE clause")

    if mutating:
        if step.max_affected_rows is None:
            raise TransactionValidationError(f"{kind.upper()} requires max_affected_rows")
        if step.max_affected_rows <= 0:
            raise TransactionValidationError("max_affected_rows must be greater than zero")

    return kind, mutating


def validate_transaction_steps(
    steps: list[TransactionStep],
    *,
    read_only: bool,
    absolute_max_rows: int,
) -> list[ValidatedTransactionStep]:
    """Validate all steps before checking out a database connection."""
    if not steps:
        raise TransactionValidationError("at least one transaction step is required")
    if absolute_max_rows <= 0:
        raise TransactionValidationError("absolute_max_rows must be greater than zero")

    validated: list[ValidatedTransactionStep] = []
    for index, step in enumerate(steps):
        if step.max_rows <= 0 or step.max_rows > absolute_max_rows:
            raise TransactionValidationError(f"step {index}: max_rows must be between 1 and {absolute_max_rows}")
        if step.expected_rows is not None and step.expected_rows < 0:
            raise TransactionValidationError(f"step {index}: expected_rows cannot be negative")
        if (
            step.expected_rows is not None
            and step.max_affected_rows is not None
            and step.expected_rows > step.max_affected_rows
        ):
            raise TransactionValidationError(f"step {index}: expected_rows cannot exceed max_affected_rows")

        statement = parse_single_statement(step.sql, parameter_count=len(step.params))
        try:
            kind, mutating = _validate_statement_policy(statement, step, read_only=read_only)
        except TransactionValidationError as exc:
            raise TransactionValidationError(f"step {index}: {exc}") from exc
        validated.append(ValidatedTransactionStep(step=step, statement_kind=kind, mutating=mutating))
    return validated
