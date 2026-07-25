"""PostgreSQL adapter for typed, guarded data operations."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from typing import TypeVar

from psycopg import InterfaceError
from psycopg import OperationalError
from psycopg.rows import dict_row
from psycopg.sql import SQL
from psycopg.sql import Composable
from psycopg.sql import Identifier
from psycopg.sql import Placeholder

from postgres_mcp.runtime import DEFAULT_LOCK_TIMEOUT_SECONDS
from postgres_mcp.runtime import DEFAULT_QUERY_TIMEOUT_SECONDS
from postgres_mcp.sql import SqlDriver
from postgres_mcp.sql import json_text

from .domain import MAX_DATA_RESULT_BYTES
from .domain import ComparisonOperator
from .domain import DataConflictError
from .domain import DataExecutionError
from .domain import DataValidationError
from .domain import DeleteRowsRequest
from .domain import FilterCondition
from .domain import FilterSet
from .domain import InsertRowsRequest
from .domain import MutationGuard
from .domain import MutationResult
from .domain import OrderDirection
from .domain import OrderTerm
from .domain import PageCursor
from .domain import QualifiedRelation
from .domain import RowPage
from .domain import SelectRowsRequest
from .domain import UpdateRowsRequest
from .domain import UpsertRowsRequest

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class ColumnMetadata:
    name: str
    not_null: bool
    has_default: bool
    identity_kind: str
    generated_kind: str

    @property
    def generated(self) -> bool:
        return bool(self.generated_kind)

    @property
    def identity_always(self) -> bool:
        return self.identity_kind == "a"

    @property
    def identity(self) -> bool:
        return bool(self.identity_kind)


@dataclass(frozen=True, slots=True)
class RelationMetadata:
    oid: int
    relation_kind: str
    columns: dict[str, ColumnMetadata]
    unique_keys: tuple[tuple[str, ...], ...]
    primary_key: tuple[str, ...]


_OPERATOR_SQL: dict[ComparisonOperator, Composable] = {
    ComparisonOperator.EQ: SQL("="),
    ComparisonOperator.NE: SQL("<>"),
    ComparisonOperator.LT: SQL("<"),
    ComparisonOperator.LTE: SQL("<="),
    ComparisonOperator.GT: SQL(">"),
    ComparisonOperator.GTE: SQL(">="),
    ComparisonOperator.LIKE: SQL("LIKE"),
    ComparisonOperator.ILIKE: SQL("ILIKE"),
}
_DIRECTION_SQL: dict[OrderDirection, Composable] = {
    OrderDirection.ASC: SQL("ASC"),
    OrderDirection.DESC: SQL("DESC"),
}


class PostgresDataRepository:
    """Catalog-validated PostgreSQL implementation of the data repository port."""

    def __init__(
        self,
        sql_driver: SqlDriver,
        *,
        timeout_seconds: float = DEFAULT_QUERY_TIMEOUT_SECONDS,
        lock_timeout_seconds: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
    ):
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        if lock_timeout_seconds <= 0:
            raise ValueError("lock_timeout_seconds must be greater than zero")
        self._driver = sql_driver
        self._timeout_seconds = timeout_seconds
        self._lock_timeout_seconds = lock_timeout_seconds

    @staticmethod
    async def _rollback(connection: Any) -> bool:
        try:
            await asyncio.shield(connection.rollback())
        except Exception:
            return False
        return True

    async def _transaction(self, *, read_only: bool, operation: Callable[[Any], Awaitable[T]]) -> T:
        rolled_back: bool | None = None
        try:
            async with asyncio.timeout(self._timeout_seconds):
                async with self._driver.connection() as connection:
                    started = False
                    try:
                        async with connection.cursor(row_factory=dict_row) as cursor:
                            started = True
                            await cursor.execute(
                                "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY" if read_only else "BEGIN ISOLATION LEVEL SERIALIZABLE READ WRITE"
                            )
                            await cursor.execute(
                                "SELECT set_config('statement_timeout', %s, true)",
                                [f"{max(1, int(self._timeout_seconds * 1000))}ms"],
                            )
                            await cursor.execute(
                                "SELECT set_config('lock_timeout', %s, true)",
                                [f"{max(1, int(self._lock_timeout_seconds * 1000))}ms"],
                            )
                            await cursor.execute(
                                "SELECT set_config('idle_in_transaction_session_timeout', %s, true)",
                                [f"{max(1, int(self._timeout_seconds * 1000))}ms"],
                            )
                            await cursor.execute("SELECT set_config('row_security', 'on', true)")
                            await cursor.execute("SELECT set_config('search_path', 'pg_catalog', true)")
                            await cursor.execute("SELECT set_config('application_name', 'pgsql-mcp:data-operations', true)")
                            result = await operation(cursor)

                        if read_only:
                            rolled_back = await self._rollback(connection)
                            started = False
                            if not rolled_back:
                                raise DataExecutionError(
                                    "read-only operation completed but transaction cleanup could not be confirmed",
                                    rolled_back=False,
                                )
                            return result

                        try:
                            await connection.commit()
                        except (OperationalError, InterfaceError) as exc:
                            rolled_back = await self._rollback(connection)
                            started = False
                            raise DataExecutionError(
                                "commit outcome is unknown; verify database state before retrying",
                                commit_state="unknown",
                                rolled_back=rolled_back,
                            ) from exc
                        except Exception as exc:
                            rolled_back = await self._rollback(connection)
                            started = False
                            raise DataExecutionError(
                                "data operation was not committed",
                                rolled_back=rolled_back,
                            ) from exc
                        started = False
                        return result
                    except asyncio.CancelledError:
                        if started:
                            rolled_back = await self._rollback(connection)
                        raise
                    except DataExecutionError as exc:
                        if started:
                            rolled_back = await self._rollback(connection)
                            if not rolled_back:
                                raise DataExecutionError(
                                    "operation failed and rollback could not be confirmed",
                                    rolled_back=False,
                                ) from exc
                        raise
                    except BaseException as exc:
                        if started:
                            rolled_back = await self._rollback(connection)
                            if not rolled_back:
                                raise DataExecutionError(
                                    "operation failed and rollback could not be confirmed",
                                    rolled_back=False,
                                ) from exc
                        if isinstance(exc, (DataConflictError, DataValidationError)):
                            raise
                        if not isinstance(exc, Exception):
                            raise
                        raise DataExecutionError(
                            "PostgreSQL data operation failed and was rolled back",
                            rolled_back=rolled_back,
                        ) from exc
        except TimeoutError as exc:
            raise DataExecutionError(
                "data operation timed out",
                rolled_back=rolled_back,
            ) from exc

    async def _load_metadata(
        self,
        cursor: Any,
        relation: QualifiedRelation,
        *,
        privileges: tuple[str, ...],
        writable: bool,
    ) -> RelationMetadata:
        privilege_checks = (
            SQL(" AND ").join(SQL("pg_catalog.has_table_privilege(c.oid, {})").format(Placeholder()) for _ in privileges)
            if privileges
            else SQL("TRUE")
        )
        query = SQL(
            """
            SELECT c.oid, c.relkind, ({}) AS permitted
            FROM pg_catalog.pg_class AS c
            JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
            WHERE n.nspname = %s AND c.relname = %s
            """
        ).format(privilege_checks)
        await cursor.execute(query, [*privileges, relation.schema, relation.name])
        row = await cursor.fetchone()
        if row is None:
            raise DataValidationError(f"relation {relation.display_name} was not found")
        relation_kind = str(row["relkind"])
        allowed_kinds = {"r", "p"} if writable else {"r", "p", "v", "m", "f"}
        if relation_kind not in allowed_kinds:
            mode = "mutation" if writable else "selection"
            raise DataValidationError(f"relation {relation.display_name} does not support structured {mode}")
        if not row["permitted"]:
            required = ", ".join(privileges)
            raise DataValidationError(f"current role lacks {required} privilege on {relation.display_name}")

        relation_oid = int(row["oid"])
        await cursor.execute(
            """
            SELECT
                a.attname AS name,
                a.attnotnull AS not_null,
                a.atthasdef AS has_default,
                a.attidentity AS identity_kind,
                a.attgenerated AS generated_kind
            FROM pg_catalog.pg_attribute AS a
            WHERE a.attrelid = %s
              AND a.attnum > 0
              AND NOT a.attisdropped
            ORDER BY a.attnum
            """,
            [relation_oid],
        )
        column_rows = await cursor.fetchall()
        columns = {
            str(item["name"]): ColumnMetadata(
                name=str(item["name"]),
                not_null=bool(item["not_null"]),
                has_default=bool(item["has_default"]),
                identity_kind=str(item["identity_kind"] or ""),
                generated_kind=str(item["generated_kind"] or ""),
            )
            for item in column_rows
        }

        await cursor.execute(
            """
            SELECT
                i.indisprimary AS primary,
                pg_catalog.array_agg(a.attname ORDER BY key.ord) AS columns
            FROM pg_catalog.pg_index AS i
            CROSS JOIN LATERAL pg_catalog.unnest(i.indkey) WITH ORDINALITY AS key(attnum, ord)
            JOIN pg_catalog.pg_attribute AS a
              ON a.attrelid = i.indrelid
             AND a.attnum = key.attnum
            WHERE i.indrelid = %s
              AND i.indisunique
              AND i.indisvalid
              AND i.indisready
              AND i.indpred IS NULL
              AND i.indexprs IS NULL
              AND key.ord <= i.indnkeyatts
              AND key.attnum > 0
            GROUP BY i.indexrelid, i.indisprimary
            ORDER BY i.indisprimary DESC, i.indexrelid
            """,
            [relation_oid],
        )
        unique_rows = await cursor.fetchall()
        unique_keys = tuple(tuple(str(column) for column in item["columns"]) for item in unique_rows)
        primary_key = next((key for key, item in zip(unique_keys, unique_rows, strict=True) if item["primary"]), ())
        return RelationMetadata(
            oid=relation_oid,
            relation_kind=relation_kind,
            columns=columns,
            unique_keys=unique_keys,
            primary_key=primary_key,
        )

    @staticmethod
    def _column(metadata: RelationMetadata, name: str) -> ColumnMetadata:
        try:
            return metadata.columns[name]
        except KeyError as exc:
            raise DataValidationError(f"unknown column: {name}") from exc

    def _validate_projection(self, metadata: RelationMetadata, columns: tuple[str, ...]) -> tuple[str, ...]:
        selected = columns or tuple(metadata.columns)
        for column in selected:
            self._column(metadata, column)
        return selected

    def _validate_insert_columns(self, metadata: RelationMetadata, columns: tuple[str, ...]) -> None:
        supplied = set(columns)
        for name in columns:
            column = self._column(metadata, name)
            if column.generated:
                raise DataValidationError(f"column {name} is generated and cannot be written")
            if column.identity_always:
                raise DataValidationError(f"column {name} is generated-always identity and cannot be written")
        required = {
            column.name
            for column in metadata.columns.values()
            if column.not_null and not column.has_default and not column.generated and not column.identity
        }
        missing = sorted(required - supplied)
        if missing:
            raise DataValidationError(f"missing required columns: {', '.join(missing)}")

    def _validate_update_columns(self, metadata: RelationMetadata, columns: tuple[str, ...]) -> None:
        for name in columns:
            column = self._column(metadata, name)
            if column.generated:
                raise DataValidationError(f"column {name} is generated and cannot be updated")
            if column.identity:
                raise DataValidationError(f"column {name} is an identity column and cannot be updated")

    def _condition(self, condition: FilterCondition, metadata: RelationMetadata) -> tuple[Composable, list[Any]]:
        self._column(metadata, condition.column)
        identifier = Identifier(condition.column)
        if condition.operator is ComparisonOperator.IS_NULL:
            return SQL("{} IS NULL").format(identifier), []
        if condition.operator is ComparisonOperator.IS_NOT_NULL:
            return SQL("{} IS NOT NULL").format(identifier), []
        if condition.operator in {ComparisonOperator.IN, ComparisonOperator.NOT_IN}:
            values = list(condition.value)
            placeholders = SQL(", ").join(Placeholder() for _ in values)
            keyword = SQL("IN") if condition.operator is ComparisonOperator.IN else SQL("NOT IN")
            return SQL("{} {} ({})").format(identifier, keyword, placeholders), values
        operator = _OPERATOR_SQL[condition.operator]
        return SQL("{} {} {}").format(identifier, operator, Placeholder()), [condition.value]

    def _filters(self, filters: FilterSet, metadata: RelationMetadata) -> tuple[Composable, list[Any]]:
        groups: list[Composable] = []
        params: list[Any] = []
        if filters.all_of:
            conditions = []
            for condition in filters.all_of:
                sql, values = self._condition(condition, metadata)
                conditions.append(sql)
                params.extend(values)
            groups.append(SQL("({})").format(SQL(" AND ").join(conditions)))
        if filters.any_of:
            conditions = []
            for condition in filters.any_of:
                sql, values = self._condition(condition, metadata)
                conditions.append(sql)
                params.extend(values)
            groups.append(SQL("({})").format(SQL(" OR ").join(conditions)))
        return (SQL(" AND ").join(groups) if groups else SQL("TRUE")), params

    def _resolve_order(self, request: SelectRowsRequest, metadata: RelationMetadata) -> tuple[OrderTerm, ...]:
        order = request.order_by or tuple(OrderTerm(column) for column in metadata.primary_key)
        if not order:
            raise DataValidationError("order_by is required because the relation has no primary key")
        order_columns = tuple(term.column for term in order)
        for column_name in order_columns:
            column = self._column(metadata, column_name)
            if not column.not_null:
                raise DataValidationError(f"order column {column_name} must be NOT NULL for stable pagination")
        ordered_set = set(order_columns)
        if not any(set(unique_key).issubset(ordered_set) for unique_key in metadata.unique_keys):
            raise DataValidationError("order_by must include a complete primary or unique key")
        return order

    def _after_cursor(
        self,
        request: SelectRowsRequest,
        metadata: RelationMetadata,
        order: tuple[OrderTerm, ...],
    ) -> tuple[Composable, list[Any]]:
        if request.cursor is None:
            return SQL("TRUE"), []
        values = PageCursor.decode(request.cursor, request.relation, order).values
        disjunctions: list[Composable] = []
        params: list[Any] = []
        for index, term in enumerate(order):
            conjunctions: list[Composable] = []
            for prior_index in range(index):
                conjunctions.append(SQL("{} = {}").format(Identifier(order[prior_index].column), Placeholder()))
                params.append(values[prior_index])
            operator = SQL(">") if term.direction is OrderDirection.ASC else SQL("<")
            conjunctions.append(SQL("{} {} {}").format(Identifier(term.column), operator, Placeholder()))
            params.append(values[index])
            disjunctions.append(SQL("({})").format(SQL(" AND ").join(conjunctions)))
        return SQL("({})").format(SQL(" OR ").join(disjunctions)), params

    @staticmethod
    def _encoded_bytes(value: Any) -> int:
        return len(json_text(value).encode("utf-8"))

    @classmethod
    def _bounded_page_rows(
        cls,
        rows: list[dict[str, Any]],
        *,
        limit: int,
        hidden: dict[str, str],
    ) -> tuple[list[dict[str, Any]], int | None, str | None]:
        visible: list[dict[str, Any]] = []
        encoded_bytes = 2
        stop_index: int | None = None
        reason: str | None = None
        hidden_names = frozenset(hidden.values())
        for index, raw in enumerate(rows[:limit]):
            row = {key: value for key, value in raw.items() if key not in hidden_names}
            row_bytes = cls._encoded_bytes(row) + (1 if visible else 0)
            if encoded_bytes + row_bytes > MAX_DATA_RESULT_BYTES:
                if not visible:
                    raise DataValidationError("a single selected row exceeds the structured response byte limit; narrow the projection")
                stop_index = index
                reason = "byte_limit"
                break
            visible.append(row)
            encoded_bytes += row_bytes
        return visible, stop_index, reason

    @staticmethod
    async def _guarded_mutation(cursor: Any, guard: MutationGuard, returning: tuple[str, ...]) -> MutationResult:
        rowcount = getattr(cursor, "rowcount", None)
        if not isinstance(rowcount, int) or rowcount < 0:
            raise DataExecutionError("PostgreSQL did not report a reliable affected-row count")
        if guard.expected_rows is not None and rowcount != guard.expected_rows:
            raise DataConflictError(f"operation affected {rowcount} rows; expected {guard.expected_rows}")
        if rowcount > guard.max_affected_rows:
            raise DataConflictError(f"operation affected {rowcount} rows; maximum is {guard.max_affected_rows}")
        rows: tuple[dict[str, Any], ...] = ()
        if returning:
            fetched = await cursor.fetchmany(guard.max_affected_rows + 1)
            if len(fetched) > guard.max_affected_rows:
                raise DataConflictError(f"operation returned more than the maximum {guard.max_affected_rows} rows")
            rows = tuple(dict(row) for row in fetched)
            if PostgresDataRepository._encoded_bytes(rows) > MAX_DATA_RESULT_BYTES:
                raise DataValidationError(
                    "mutation returning payload exceeds the structured response byte limit; reduce returning columns or batch size"
                )
        return MutationResult(affected_rows=rowcount, rows=rows)

    async def select(self, request: SelectRowsRequest) -> RowPage:
        async def operation(cursor: Any) -> RowPage:
            metadata = await self._load_metadata(cursor, request.relation, privileges=("SELECT",), writable=False)
            selected = self._validate_projection(metadata, request.columns)
            order = self._resolve_order(request, metadata)
            base_filter, params = self._filters(request.filters, metadata)
            cursor_filter, cursor_params = self._after_cursor(request, metadata, order)
            params.extend(cursor_params)

            hidden: dict[str, str] = {}
            projection: list[Composable] = [Identifier(column) for column in selected]
            for index, term in enumerate(order):
                if term.column not in selected:
                    alias = f"__mcp_order_{index}"
                    hidden[term.column] = alias
                    projection.append(SQL("{} AS {}").format(Identifier(term.column), Identifier(alias)))
            order_sql = SQL(", ").join(SQL("{} {}").format(Identifier(term.column), _DIRECTION_SQL[term.direction]) for term in order)
            query = SQL("SELECT {} FROM {}.{} WHERE ({}) AND ({}) ORDER BY {} LIMIT {}").format(
                SQL(", ").join(projection),
                Identifier(request.relation.schema),
                Identifier(request.relation.name),
                base_filter,
                cursor_filter,
                order_sql,
                Placeholder(),
            )
            params.append(request.limit + 1)
            await cursor.execute(query, params)
            fetched = [dict(row) for row in await cursor.fetchmany(request.limit + 1)]
            visible_rows, stop_index, truncation_reason = self._bounded_page_rows(
                fetched,
                limit=request.limit,
                hidden=hidden,
            )
            consumed = len(visible_rows)
            truncated = stop_index is not None or len(fetched) > request.limit
            if truncation_reason is None and truncated:
                truncation_reason = "row_limit"
            next_cursor = None
            if truncated and consumed:
                last = fetched[consumed - 1]
                values = tuple(last[hidden.get(term.column, term.column)] for term in order)
                next_cursor = PageCursor.encode(request.relation, order, values)
            return RowPage(
                rows=tuple(visible_rows),
                next_cursor=next_cursor,
                truncated=truncated,
                truncation_reason=truncation_reason,
            )

        return await self._transaction(read_only=True, operation=operation)

    async def insert(self, request: InsertRowsRequest) -> MutationResult:
        async def operation(cursor: Any) -> MutationResult:
            metadata = await self._load_metadata(cursor, request.relation, privileges=("INSERT",), writable=True)
            columns = tuple(request.rows[0])
            self._validate_insert_columns(metadata, columns)
            values_sql = SQL(", ").join(SQL("({})").format(SQL(", ").join(Placeholder() for _ in columns)) for _ in request.rows)
            query = SQL("INSERT INTO {}.{} ({}) VALUES {}").format(
                Identifier(request.relation.schema),
                Identifier(request.relation.name),
                SQL(", ").join(Identifier(column) for column in columns),
                values_sql,
            )
            if request.returning:
                self._validate_projection(metadata, request.returning)
                query = query + SQL(" RETURNING {}").format(SQL(", ").join(Identifier(column) for column in request.returning))
            params = [row[column] for row in request.rows for column in columns]
            await cursor.execute(query, params)
            return await self._guarded_mutation(cursor, request.guard, request.returning)

        return await self._transaction(read_only=False, operation=operation)

    async def upsert(self, request: UpsertRowsRequest) -> MutationResult:
        async def operation(cursor: Any) -> MutationResult:
            metadata = await self._load_metadata(cursor, request.relation, privileges=("INSERT", "UPDATE"), writable=True)
            columns = tuple(request.rows[0])
            self._validate_insert_columns(metadata, columns)
            self._validate_update_columns(metadata, request.update_columns)
            if not any(set(key) == set(request.conflict_columns) for key in metadata.unique_keys):
                raise DataValidationError("conflict_columns must match a non-partial primary or unique key")
            values_sql = SQL(", ").join(SQL("({})").format(SQL(", ").join(Placeholder() for _ in columns)) for _ in request.rows)
            query = SQL("INSERT INTO {}.{} ({}) VALUES {} ON CONFLICT ({}) ").format(
                Identifier(request.relation.schema),
                Identifier(request.relation.name),
                SQL(", ").join(Identifier(column) for column in columns),
                values_sql,
                SQL(", ").join(Identifier(column) for column in request.conflict_columns),
            )
            if request.update_columns:
                assignments = SQL(", ").join(
                    SQL("{} = EXCLUDED.{}").format(Identifier(column), Identifier(column)) for column in request.update_columns
                )
                query = query + SQL("DO UPDATE SET {}").format(assignments)
            else:
                query = query + SQL("DO NOTHING")
            if request.returning:
                self._validate_projection(metadata, request.returning)
                query = query + SQL(" RETURNING {}").format(SQL(", ").join(Identifier(column) for column in request.returning))
            params = [row[column] for row in request.rows for column in columns]
            await cursor.execute(query, params)
            return await self._guarded_mutation(cursor, request.guard, request.returning)

        return await self._transaction(read_only=False, operation=operation)

    async def update(self, request: UpdateRowsRequest) -> MutationResult:
        async def operation(cursor: Any) -> MutationResult:
            metadata = await self._load_metadata(cursor, request.relation, privileges=("UPDATE",), writable=True)
            columns = tuple(request.values)
            self._validate_update_columns(metadata, columns)
            base_filter, filter_params = self._filters(request.filters, metadata)
            concurrency_filter, concurrency_params = self._filters(request.concurrency, metadata)
            assignments = SQL(", ").join(SQL("{} = {}").format(Identifier(column), Placeholder()) for column in columns)
            query = SQL("UPDATE {}.{} SET {} WHERE ({}) AND ({})").format(
                Identifier(request.relation.schema),
                Identifier(request.relation.name),
                assignments,
                base_filter,
                concurrency_filter,
            )
            if request.returning:
                self._validate_projection(metadata, request.returning)
                query = query + SQL(" RETURNING {}").format(SQL(", ").join(Identifier(column) for column in request.returning))
            params = [request.values[column] for column in columns] + filter_params + concurrency_params
            await cursor.execute(query, params)
            return await self._guarded_mutation(cursor, request.guard, request.returning)

        return await self._transaction(read_only=False, operation=operation)

    async def delete(self, request: DeleteRowsRequest) -> MutationResult:
        async def operation(cursor: Any) -> MutationResult:
            metadata = await self._load_metadata(cursor, request.relation, privileges=("DELETE",), writable=True)
            base_filter, filter_params = self._filters(request.filters, metadata)
            concurrency_filter, concurrency_params = self._filters(request.concurrency, metadata)
            query = SQL("DELETE FROM {}.{} WHERE ({}) AND ({})").format(
                Identifier(request.relation.schema),
                Identifier(request.relation.name),
                base_filter,
                concurrency_filter,
            )
            if request.returning:
                self._validate_projection(metadata, request.returning)
                query = query + SQL(" RETURNING {}").format(SQL(", ").join(Identifier(column) for column in request.returning))
            await cursor.execute(query, filter_params + concurrency_params)
            return await self._guarded_mutation(cursor, request.guard, request.returning)

        return await self._transaction(read_only=False, operation=operation)
