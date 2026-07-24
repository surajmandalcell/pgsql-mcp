"""Conservative guard for user-supplied read-only SQL."""

from __future__ import annotations

import asyncio
from typing import Any

from pglast.ast import ExplainStmt
from pglast.ast import FuncCall
from pglast.ast import Node
from pglast.ast import SelectStmt
from pglast.ast import VariableShowStmt
from typing_extensions import LiteralString

from .results import BoundedQueryResult
from .safe_sql import SafeSqlDriver
from .sql_driver import SqlDriver
from .transaction import TransactionValidationError
from .transaction import parse_single_statement
from .transaction import sql_for_validation

MAX_SQL_CHARACTERS = 100_000

_PUBLIC_READONLY_STATEMENTS = (SelectStmt, ExplainStmt, VariableShowStmt)
_SESSION_MUTATING_FUNCTIONS = frozenset(
    {
        "hypopg_create_index",
        "hypopg_hide_index",
        "hypopg_reset",
        "hypopg_unhide_index",
        "setseed",
    }
)


def _qualified_function_name(node: FuncCall) -> str:
    return ".".join(str(part.sval) for part in node.funcname or ()).lower()


def _reject_session_mutation(node: Any) -> None:
    if isinstance(node, FuncCall):
        qualified = _qualified_function_name(node)
        unqualified = qualified.rsplit(".", maxsplit=1)[-1]
        if unqualified in _SESSION_MUTATING_FUNCTIONS:
            raise TransactionValidationError(f"function '{qualified}' is not allowed in public read-only queries")
    if isinstance(node, Node):
        for attribute_name in node.__slots__:
            if attribute_name.startswith("_"):
                continue
            try:
                value = getattr(node, attribute_name)
            except AttributeError:
                continue
            _reject_session_mutation(value)
    elif isinstance(node, (list, tuple)):
        for item in node:
            _reject_session_mutation(item)


class SafeQueryValidator(SafeSqlDriver):
    """Expose the existing deep AST validator through a public package API."""

    def validate_query(self, query: str, *, parameter_count: int) -> None:
        """Validate one query after safely normalizing client placeholders."""
        validation_query = sql_for_validation(query, parameter_count=parameter_count)
        statement = parse_single_statement(query, parameter_count=parameter_count)
        if not isinstance(statement, _PUBLIC_READONLY_STATEMENTS):
            statement_name = type(statement).__name__.removesuffix("Stmt").lower()
            raise TransactionValidationError(f"statement type '{statement_name}' is not allowed in public read-only queries")
        _reject_session_mutation(statement)
        self._validate(validation_query)  # pyright: ignore[reportPrivateUsage]


class SafeQueryExecutor:
    """Run a single validated statement with total validation/execution limits."""

    def __init__(self, sql_driver: SqlDriver, *, timeout_seconds: float):
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        self.sql_driver = sql_driver
        self.timeout_seconds = timeout_seconds
        self.validator = SafeQueryValidator(sql_driver=sql_driver, timeout=timeout_seconds)

    @staticmethod
    def _check_query_size(query: str) -> None:
        if len(query) > MAX_SQL_CHARACTERS:
            raise ValueError(f"SQL cannot exceed {MAX_SQL_CHARACTERS} characters")

    async def _validate_in_worker(self, query: str, *, parameter_count: int) -> None:
        # pglast parsing is synchronous. Offloading it keeps the event loop
        # cancellable so the surrounding timeout covers pathological input.
        await asyncio.to_thread(
            self.validator.validate_query,
            query,
            parameter_count=parameter_count,
        )

    async def validate_query(self, query: str, *, parameter_count: int = 0) -> None:
        """Validate a query within the same time budget used by execution."""
        self._check_query_size(query)
        try:
            async with asyncio.timeout(self.timeout_seconds):
                await self._validate_in_worker(query, parameter_count=parameter_count)
        except TimeoutError as exc:
            raise ValueError(f"query validation timed out after {self.timeout_seconds:g} seconds") from exc

    async def execute_bounded_query(
        self,
        query: LiteralString,
        *,
        params: list[Any] | None,
        max_rows: int,
    ) -> BoundedQueryResult:
        """Validate and execute exactly one database-enforced read-only query."""
        self._check_query_size(query)
        parameter_count = len(params) if params is not None else 0
        try:
            async with asyncio.timeout(self.timeout_seconds):
                await self._validate_in_worker(query, parameter_count=parameter_count)
                return await self.sql_driver.execute_bounded_query(
                    query,
                    params=params,
                    max_rows=max_rows,
                    force_readonly=True,
                    timeout_seconds=self.timeout_seconds,
                )
        except TimeoutError as exc:
            raise ValueError(
                f"query validation or execution timed out after {self.timeout_seconds:g} seconds"
            ) from exc
