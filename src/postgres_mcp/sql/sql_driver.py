"""SQL driver adapter for PostgreSQL connections."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse
from urllib.parse import urlunparse

from psycopg import InterfaceError
from psycopg import OperationalError
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from typing_extensions import LiteralString

from postgres_mcp.runtime import ABSOLUTE_MAX_ROWS
from postgres_mcp.runtime import DEFAULT_LOCK_TIMEOUT_SECONDS
from postgres_mcp.runtime import DEFAULT_POOL_MAX_SIZE
from postgres_mcp.runtime import DEFAULT_POOL_MIN_SIZE

from .results import BoundedQueryResult
from .results import column_info_from_description
from .transaction import IsolationLevel
from .transaction import ResultMode
from .transaction import TransactionExecutionError
from .transaction import TransactionExecutionResult
from .transaction import TransactionStep
from .transaction import TransactionStepResult
from .transaction import ValidatedTransactionStep
from .transaction import validate_transaction_steps

logger = logging.getLogger(__name__)


def obfuscate_password(text: str | None) -> str | None:
    """Obfuscate passwords in URLs, DSNs, and arbitrary error text."""
    if text is None or not text:
        return text

    try:
        parsed = urlparse(text)
        if parsed.scheme and parsed.netloc and parsed.password:
            netloc = parsed.netloc.replace(parsed.password, "****")
            return urlunparse(parsed._replace(netloc=netloc))
    except Exception:
        pass

    url_pattern = re.compile(r"(postgres(?:ql)?:\/\/[^:]+:)([^@]+)(@[^\/\s]+)")
    text = re.sub(url_pattern, r"\1****\3", text)
    param_pattern = re.compile(r'(password=)([^\s&;"\']+)', re.IGNORECASE)
    text = re.sub(param_pattern, r"\1****", text)
    dsn_single_quote = re.compile(r"(password\s*=\s*')([^']+)(')", re.IGNORECASE)
    text = re.sub(dsn_single_quote, r"\1****\3", text)
    dsn_double_quote = re.compile(r'(password\s*=\s*")([^"]+)(")', re.IGNORECASE)
    return re.sub(dsn_double_quote, r"\1****\3", text)


class DbConnPool:
    """Lazy PostgreSQL connection pool with explicit lifecycle state."""

    def __init__(
        self,
        connection_url: str | None = None,
        *,
        min_size: int = DEFAULT_POOL_MIN_SIZE,
        max_size: int = DEFAULT_POOL_MAX_SIZE,
    ):
        if min_size < 0:
            raise ValueError("pool min_size cannot be negative")
        if max_size <= 0 or max_size < min_size:
            raise ValueError("pool max_size must be positive and at least min_size")
        self.connection_url = connection_url
        self.min_size = min_size
        self.max_size = max_size
        self.pool: AsyncConnectionPool | None = None
        self._is_valid = False
        self._last_error: str | None = None

    async def pool_connect(self, connection_url: str | None = None) -> AsyncConnectionPool:
        """Initialize and verify the connection pool."""
        if self.pool and self._is_valid:
            return self.pool

        url = connection_url or self.connection_url
        self.connection_url = url
        if not url:
            self._is_valid = False
            self._last_error = "Database connection URL not provided"
            raise ValueError(self._last_error)

        await self.close()
        try:
            self.pool = AsyncConnectionPool(
                conninfo=url,
                min_size=self.min_size,
                max_size=self.max_size,
                open=False,
            )
            await self.pool.open()
            async with self.pool.connection() as conn:
                async with conn.cursor() as cursor:
                    await cursor.execute("SELECT 1")
            self._is_valid = True
            self._last_error = None
            return self.pool
        except Exception as exc:
            self._is_valid = False
            self._last_error = str(exc)
            await self.close()
            raise ValueError(f"Connection attempt failed: {obfuscate_password(str(exc))}") from exc

    async def close(self) -> None:
        """Close the pool and clear reusable state."""
        pool = self.pool
        self.pool = None
        self._is_valid = False
        if pool is not None:
            try:
                await pool.close()
            except Exception as exc:
                logger.warning("Error closing connection pool: %s", exc)

    def mark_invalid(self, error: BaseException) -> None:
        """Record a connection-level failure without exposing mutable internals."""
        self._is_valid = False
        self._last_error = str(error)

    @property
    def is_valid(self) -> bool:
        return self._is_valid

    @property
    def last_error(self) -> str | None:
        return self._last_error


class SqlDriver:
    """PostgreSQL execution adapter used by MCP tools and analyzers."""

    @dataclass
    class RowResult:
        cells: dict[str, Any]

    def __init__(self, conn: Any = None, engine_url: str | None = None):
        if conn is not None:
            self.conn = conn
            self.is_pool = isinstance(conn, DbConnPool)
            self.engine_url = None
        elif engine_url:
            self.engine_url = engine_url
            self.conn = None
            self.is_pool = False
        else:
            raise ValueError("Either conn or engine_url must be provided")

    def connect(self) -> Any:
        if self.conn is not None:
            return self.conn
        if self.engine_url:
            self.conn = DbConnPool(self.engine_url)
            self.is_pool = True
            return self.conn
        raise ValueError("Connection not established. Either conn or engine_url must be provided")

    @asynccontextmanager
    async def _connection(self) -> AsyncIterator[Any]:
        if self.conn is None:
            self.connect()
        if self.conn is None:
            raise ValueError("Connection not established")

        if self.is_pool:
            pool = await self.conn.pool_connect()
            async with pool.connection() as connection:
                yield connection
        else:
            yield self.conn

    def _mark_connection_failure(self, exc: Exception) -> None:
        root_error: BaseException = exc
        while root_error.__cause__ is not None:
            root_error = root_error.__cause__
        if not isinstance(root_error, (OperationalError, InterfaceError)):
            return
        if self.conn is not None and self.is_pool:
            self.conn.mark_invalid(root_error)
        elif self.conn is not None:
            self.conn = None

    async def execute_query(
        self,
        query: LiteralString,
        params: list[Any] | None = None,
        force_readonly: bool = False,
    ) -> list[RowResult] | None:
        """Execute a compatibility query and return all rows.

        New user-facing code should prefer :meth:`execute_bounded_query`; this
        method remains for internal analyzers whose existing contracts expect a
        complete result set.
        """
        try:
            async with self._connection() as connection:
                return await self._execute_with_connection(connection, query, params, force_readonly=force_readonly)
        except Exception as exc:
            self._mark_connection_failure(exc)
            raise

    async def _execute_with_connection(
        self,
        connection: Any,
        query: LiteralString,
        params: list[Any] | None,
        force_readonly: bool,
    ) -> list[RowResult] | None:
        transaction_started = False
        try:
            async with connection.cursor(row_factory=dict_row) as cursor:
                if force_readonly:
                    await cursor.execute("BEGIN TRANSACTION READ ONLY")
                    transaction_started = True

                if params:
                    await cursor.execute(query, params)
                else:
                    await cursor.execute(query)

                while await cursor.nextset():
                    pass

                if cursor.description is None:
                    if force_readonly and transaction_started:
                        await cursor.execute("ROLLBACK")
                        transaction_started = False
                    elif not force_readonly:
                        await cursor.execute("COMMIT")
                    return None

                rows = await cursor.fetchall()
                if force_readonly and transaction_started:
                    await cursor.execute("ROLLBACK")
                    transaction_started = False
                elif not force_readonly:
                    await cursor.execute("COMMIT")
                return [SqlDriver.RowResult(cells=dict(row)) for row in rows]
        except BaseException:
            if transaction_started:
                try:
                    await connection.rollback()
                except Exception as rollback_error:
                    logger.error("Error rolling back transaction: %s", rollback_error)
            logger.exception("Error executing query")
            raise

    async def execute_bounded_query(
        self,
        query: LiteralString,
        *,
        params: list[Any] | None = None,
        max_rows: int,
        force_readonly: bool,
        timeout_seconds: float | None = None,
    ) -> BoundedQueryResult:
        """Execute one query while enforcing a hard result-row ceiling."""
        if max_rows <= 0 or max_rows > ABSOLUTE_MAX_ROWS:
            raise ValueError(f"max_rows must be between 1 and {ABSOLUTE_MAX_ROWS}")
        if timeout_seconds is not None and timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        try:
            async with self._connection() as connection:
                return await self._execute_bounded_with_connection(
                    connection,
                    query,
                    params=params,
                    max_rows=max_rows,
                    force_readonly=force_readonly,
                    timeout_seconds=timeout_seconds,
                )
        except Exception as exc:
            self._mark_connection_failure(exc)
            raise

    async def _execute_bounded_with_connection(
        self,
        connection: Any,
        query: LiteralString,
        *,
        params: list[Any] | None,
        max_rows: int,
        force_readonly: bool,
        timeout_seconds: float | None,
    ) -> BoundedQueryResult:
        transaction_started = False
        try:
            async with connection.cursor(row_factory=dict_row) as cursor:
                await cursor.execute("BEGIN TRANSACTION READ ONLY" if force_readonly else "BEGIN TRANSACTION")
                transaction_started = True
                if timeout_seconds is not None:
                    timeout_ms = max(1, int(timeout_seconds * 1000))
                    lock_timeout_ms = max(1, int(min(timeout_seconds, DEFAULT_LOCK_TIMEOUT_SECONDS) * 1000))
                    await cursor.execute(
                        "SELECT set_config('statement_timeout', %s, true)",
                        [f"{timeout_ms}ms"],
                    )
                    await cursor.execute(
                        "SELECT set_config('lock_timeout', %s, true)",
                        [f"{lock_timeout_ms}ms"],
                    )
                    await cursor.execute(
                        "SELECT set_config('idle_in_transaction_session_timeout', %s, true)",
                        [f"{timeout_ms}ms"],
                    )
                await cursor.execute("SELECT set_config('row_security', 'on', true)")
                await cursor.execute("SELECT set_config('search_path', 'pg_catalog, public', true)")

                if params:
                    await cursor.execute(query, params)
                else:
                    await cursor.execute(query)

                command = _command_name(query)
                affected_rows = _normalized_rowcount(getattr(cursor, "rowcount", None))
                if cursor.description is None:
                    if force_readonly:
                        await connection.rollback()
                    else:
                        await connection.commit()
                    transaction_started = False
                    return BoundedQueryResult(
                        rows=[],
                        columns=[],
                        row_count=0,
                        truncated=False,
                        affected_rows=affected_rows,
                        command=command,
                    )

                fetched = await cursor.fetchmany(max_rows + 1)
                truncated = len(fetched) > max_rows
                visible_rows = fetched[:max_rows]
                rows = [dict(row) for row in visible_rows]
                columns = [column_info_from_description(item) for item in cursor.description]
                if force_readonly:
                    await connection.rollback()
                else:
                    await connection.commit()
                transaction_started = False
                return BoundedQueryResult(
                    rows=rows,
                    columns=columns,
                    row_count=len(rows),
                    truncated=truncated,
                    affected_rows=affected_rows,
                    command=command,
                )
        except BaseException:
            if transaction_started:
                try:
                    await connection.rollback()
                except Exception as rollback_error:
                    logger.error("Error rolling back bounded query: %s", rollback_error)
            raise

    async def execute_transaction(
        self,
        steps: list[TransactionStep],
        *,
        isolation: IsolationLevel,
        read_only: bool,
        timeout_seconds: float,
        lock_timeout_seconds: float,
        absolute_max_rows: int = ABSOLUTE_MAX_ROWS,
    ) -> TransactionExecutionResult:
        """Execute all validated steps on one connection and one transaction."""
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        if lock_timeout_seconds <= 0:
            raise ValueError("lock_timeout_seconds must be greater than zero")
        validated_steps = validate_transaction_steps(
            steps,
            read_only=read_only,
            absolute_max_rows=absolute_max_rows,
        )
        try:
            async with asyncio.timeout(timeout_seconds):
                async with self._connection() as connection:
                    return await self._execute_transaction_with_connection(
                        connection,
                        validated_steps,
                        isolation=isolation,
                        read_only=read_only,
                        timeout_seconds=timeout_seconds,
                        lock_timeout_seconds=lock_timeout_seconds,
                    )
        except TimeoutError as exc:
            raise TransactionExecutionError("transaction timed out and was rolled back") from exc
        except Exception as exc:
            self._mark_connection_failure(exc)
            raise

    async def _execute_transaction_with_connection(
        self,
        connection: Any,
        steps: list[ValidatedTransactionStep],
        *,
        isolation: IsolationLevel,
        read_only: bool,
        timeout_seconds: float,
        lock_timeout_seconds: float,
    ) -> TransactionExecutionResult:
        transaction_started = False
        active_step: int | None = None
        results: list[TransactionStepResult] = []
        try:
            async with connection.cursor() as control_cursor:
                access_clause = "READ ONLY" if read_only else "READ WRITE"
                await control_cursor.execute(f"BEGIN ISOLATION LEVEL {isolation.sql} {access_clause}")
                transaction_started = True
                await control_cursor.execute(
                    "SELECT set_config('statement_timeout', %s, true)",
                    [f"{max(1, int(timeout_seconds * 1000))}ms"],
                )
                await control_cursor.execute(
                    "SELECT set_config('lock_timeout', %s, true)",
                    [f"{max(1, int(lock_timeout_seconds * 1000))}ms"],
                )
                await control_cursor.execute(
                    "SELECT set_config('idle_in_transaction_session_timeout', %s, true)",
                    [f"{max(1, int(timeout_seconds * 1000))}ms"],
                )
                await control_cursor.execute("SELECT set_config('row_security', 'on', true)")
                await control_cursor.execute("SELECT set_config('search_path', 'pg_catalog, public', true)")

            for index, validated in enumerate(steps):
                active_step = index
                step = validated.step
                async with connection.cursor(row_factory=dict_row) as cursor:
                    if step.params:
                        await cursor.execute(step.sql, list(step.params))
                    else:
                        await cursor.execute(step.sql)

                    affected_rows = _normalized_rowcount(getattr(cursor, "rowcount", None))
                    if validated.mutating and affected_rows is None:
                        raise TransactionExecutionError(
                            f"step {index} did not report a reliable affected-row count",
                            failed_step=index,
                        )
                    if step.expected_rows is not None and affected_rows != step.expected_rows:
                        raise TransactionExecutionError(
                            f"step {index} affected {affected_rows!r} rows; expected {step.expected_rows}",
                            failed_step=index,
                        )
                    if step.max_affected_rows is not None and affected_rows is not None and affected_rows > step.max_affected_rows:
                        raise TransactionExecutionError(
                            f"step {index} affected {affected_rows} rows; maximum is {step.max_affected_rows}",
                            failed_step=index,
                        )

                    rows: list[dict[str, Any]] = []
                    columns = []
                    truncated = False
                    if cursor.description is not None and step.result_mode is ResultMode.ROWS:
                        fetched = await cursor.fetchmany(step.max_rows + 1)
                        truncated = len(fetched) > step.max_rows
                        rows = [dict(row) for row in fetched[: step.max_rows]]
                        columns = [column_info_from_description(item) for item in cursor.description]

                    results.append(
                        TransactionStepResult(
                            index=index,
                            statement_kind=validated.statement_kind,
                            affected_rows=affected_rows,
                            columns=columns,
                            rows=rows,
                            truncated=truncated,
                        )
                    )

            await connection.commit()
            transaction_started = False
            return TransactionExecutionResult(
                committed=True,
                isolation=isolation,
                read_only=read_only,
                steps=results,
            )
        except BaseException as exc:
            if transaction_started:
                try:
                    await connection.rollback()
                except Exception as rollback_error:
                    logger.error("Error rolling back atomic transaction: %s", rollback_error)
            if isinstance(exc, asyncio.CancelledError):
                raise
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            if isinstance(exc, TransactionExecutionError):
                raise
            raise TransactionExecutionError(
                f"transaction failed: {exc}",
                failed_step=active_step,
            ) from exc


def _normalized_rowcount(value: Any) -> int | None:
    return value if isinstance(value, int) and value >= 0 else None


def _command_name(query: str) -> str:
    stripped = query.lstrip()
    if not stripped:
        return "UNKNOWN"
    return stripped.split(None, 1)[0].upper()
