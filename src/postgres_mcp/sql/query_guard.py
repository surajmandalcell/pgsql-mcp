"""Conservative guard for user-supplied read-only SQL."""

from __future__ import annotations

import asyncio
from typing import Any

from typing_extensions import LiteralString

from .results import BoundedQueryResult
from .safe_sql import SafeSqlDriver
from .sql_driver import SqlDriver
from .transaction import parse_single_statement
from .transaction import sql_for_validation


class SafeQueryValidator(SafeSqlDriver):
    """Expose the existing deep AST validator through a public package API."""

    def validate_query(self, query: str, *, parameter_count: int) -> None:
        """Validate one query after safely normalizing client placeholders."""
        self._validate(sql_for_validation(query, parameter_count=parameter_count))


class SafeQueryExecutor:
    """Run a single validated statement with row and time limits."""

    def __init__(self, sql_driver: SqlDriver, *, timeout_seconds: float):
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        self.sql_driver = sql_driver
        self.timeout_seconds = timeout_seconds
        self.validator = SafeQueryValidator(sql_driver=sql_driver, timeout=timeout_seconds)

    async def execute_bounded_query(
        self,
        query: LiteralString,
        *,
        params: list[Any] | None,
        max_rows: int,
    ) -> BoundedQueryResult:
        """Validate and execute exactly one database-enforced read-only query."""
        parameter_count = len(params) if params is not None else 0
        parse_single_statement(query, parameter_count=parameter_count)
        self.validator.validate_query(query, parameter_count=parameter_count)
        try:
            async with asyncio.timeout(self.timeout_seconds):
                return await self.sql_driver.execute_bounded_query(
                    query,
                    params=params,
                    max_rows=max_rows,
                    force_readonly=True,
                    timeout_seconds=self.timeout_seconds,
                )
        except TimeoutError as exc:
            raise ValueError(f"query execution timed out after {self.timeout_seconds:g} seconds") from exc
