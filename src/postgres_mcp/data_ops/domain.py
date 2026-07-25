"""Domain model for structured, bounded PostgreSQL data operations."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from datetime import datetime
from datetime import time
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

from postgres_mcp.runtime import ABSOLUTE_MAX_ROWS

MAX_IDENTIFIER_BYTES = 63
MAX_FILTER_TERMS = 64
MAX_DATA_ROWS = min(500, ABSOLUTE_MAX_ROWS)
MAX_COLUMNS = 128
MAX_CURSOR_CHARACTERS = 16_384
MAX_DATA_RESULT_BYTES = 512 * 1024
_MISSING = object()


class DataOperationError(Exception):
    """Base error for the data-operations bounded context."""


class DataValidationError(DataOperationError, ValueError):
    """Raised when a structured request violates a domain invariant."""


class DataConflictError(DataOperationError):
    """Raised when an optimistic or affected-row guard is not satisfied."""


class DataExecutionError(DataOperationError):
    """Raised when PostgreSQL cannot safely complete an operation."""

    def __init__(
        self,
        message: str,
        *,
        commit_state: str = "not_committed",
        rolled_back: bool | None = None,
    ) -> None:
        super().__init__(message)
        self.commit_state = commit_state
        self.rolled_back = rolled_back


class ComparisonOperator(str, Enum):
    """Supported value-bound filter operators."""

    EQ = "eq"
    NE = "ne"
    LT = "lt"
    LTE = "lte"
    GT = "gt"
    GTE = "gte"
    IN = "in"
    NOT_IN = "not_in"
    LIKE = "like"
    ILIKE = "ilike"
    IS_NULL = "is_null"
    IS_NOT_NULL = "is_not_null"


class OrderDirection(str, Enum):
    """Stable ordering direction for keyset pagination."""

    ASC = "asc"
    DESC = "desc"


def validate_identifier(value: str, *, label: str) -> str:
    """Validate one PostgreSQL identifier without interpreting it as SQL."""
    if not isinstance(value, str):
        raise DataValidationError(f"{label} must be a string")
    if not value or not value.strip():
        raise DataValidationError(f"{label} must not be empty")
    if "\x00" in value:
        raise DataValidationError(f"{label} must not contain NUL")
    if len(value.encode("utf-8")) > MAX_IDENTIFIER_BYTES:
        raise DataValidationError(f"{label} cannot exceed {MAX_IDENTIFIER_BYTES} UTF-8 bytes")
    return value


def _checked_columns(values: Sequence[str], *, label: str, allow_empty: bool = True) -> tuple[str, ...]:
    normalized = tuple(validate_identifier(value, label=f"{label} item") for value in values)
    if not allow_empty and not normalized:
        raise DataValidationError(f"{label} must not be empty")
    if len(normalized) > MAX_COLUMNS:
        raise DataValidationError(f"{label} cannot contain more than {MAX_COLUMNS} columns")
    if len(set(normalized)) != len(normalized):
        raise DataValidationError(f"{label} contains duplicate columns")
    return normalized


@dataclass(frozen=True, slots=True)
class QualifiedRelation:
    """A schema-qualified relation identity."""

    schema: str
    name: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "schema", validate_identifier(self.schema, label="schema"))
        object.__setattr__(self, "name", validate_identifier(self.name, label="relation"))

    @property
    def display_name(self) -> str:
        return f"{self.schema}.{self.name}"


@dataclass(frozen=True, slots=True)
class FilterCondition:
    """One value-bound column predicate."""

    column: str
    operator: ComparisonOperator
    value: Any = _MISSING

    def __post_init__(self) -> None:
        object.__setattr__(self, "column", validate_identifier(self.column, label="filter column"))
        try:
            operator = ComparisonOperator(self.operator)
        except ValueError as exc:
            raise DataValidationError(f"unsupported comparison operator: {self.operator!r}") from exc
        object.__setattr__(self, "operator", operator)

        unary = operator in {ComparisonOperator.IS_NULL, ComparisonOperator.IS_NOT_NULL}
        if unary:
            if self.value is not _MISSING:
                raise DataValidationError(f"operator {operator.value} does not accept a value")
            return
        if self.value is _MISSING or self.value is None:
            raise DataValidationError(f"operator {operator.value} requires a value; use an explicit null operator")
        if operator in {ComparisonOperator.IN, ComparisonOperator.NOT_IN}:
            if isinstance(self.value, (str, bytes, bytearray)) or not isinstance(self.value, Sequence) or not self.value:
                raise DataValidationError(f"operator {operator.value} requires a non-empty sequence")
            if any(item is None for item in self.value):
                raise DataValidationError(f"operator {operator.value} does not accept null sequence items")
            object.__setattr__(self, "value", tuple(self.value))
        elif operator in {ComparisonOperator.LIKE, ComparisonOperator.ILIKE} and not isinstance(self.value, str):
            raise DataValidationError(f"operator {operator.value} requires a string value")


@dataclass(frozen=True, slots=True)
class FilterSet:
    """A bounded `(all predicates) AND (any predicates)` expression."""

    all_of: tuple[FilterCondition, ...] = ()
    any_of: tuple[FilterCondition, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "all_of", tuple(self.all_of))
        object.__setattr__(self, "any_of", tuple(self.any_of))
        if any(not isinstance(item, FilterCondition) for item in (*self.all_of, *self.any_of)):
            raise DataValidationError("filters must contain FilterCondition values")
        if self.term_count > MAX_FILTER_TERMS:
            raise DataValidationError(f"filter term maximum is {MAX_FILTER_TERMS}")

    @property
    def term_count(self) -> int:
        return len(self.all_of) + len(self.any_of)

    @property
    def is_empty(self) -> bool:
        return self.term_count == 0


@dataclass(frozen=True, slots=True)
class OrderTerm:
    """One deterministic ordering term."""

    column: str
    direction: OrderDirection = OrderDirection.ASC

    def __post_init__(self) -> None:
        object.__setattr__(self, "column", validate_identifier(self.column, label="order column"))
        try:
            object.__setattr__(self, "direction", OrderDirection(self.direction))
        except ValueError as exc:
            raise DataValidationError(f"unsupported order direction: {self.direction!r}") from exc


@dataclass(frozen=True, slots=True)
class MutationGuard:
    """Commit preconditions for every mutation."""

    max_affected_rows: int
    expected_rows: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.max_affected_rows, int) or isinstance(self.max_affected_rows, bool):
            raise DataValidationError("max_affected_rows must be an integer")
        if self.max_affected_rows <= 0 or self.max_affected_rows > MAX_DATA_ROWS:
            raise DataValidationError(f"max_affected_rows must be between 1 and {MAX_DATA_ROWS}")
        if self.expected_rows is not None:
            if not isinstance(self.expected_rows, int) or isinstance(self.expected_rows, bool):
                raise DataValidationError("expected_rows must be an integer")
            if self.expected_rows < 0:
                raise DataValidationError("expected_rows cannot be negative")
            if self.expected_rows > self.max_affected_rows:
                raise DataValidationError("expected_rows cannot exceed max_affected_rows")


@dataclass(frozen=True, slots=True)
class _CursorPayload:
    values: tuple[Any, ...]


def _encode_cursor_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise DataValidationError("page cursor values must be finite")
        return value
    if isinstance(value, Decimal):
        return {"type": "decimal", "value": str(value)}
    if isinstance(value, datetime):
        return {"type": "datetime", "value": value.isoformat()}
    if isinstance(value, date):
        return {"type": "date", "value": value.isoformat()}
    if isinstance(value, time):
        return {"type": "time", "value": value.isoformat()}
    if isinstance(value, UUID):
        return {"type": "uuid", "value": str(value)}
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"type": "bytes", "value": base64.urlsafe_b64encode(bytes(value)).decode("ascii")}
    raise DataValidationError(f"unsupported page cursor value type: {type(value).__qualname__}")


def _decode_cursor_value(value: Any) -> Any:
    if not isinstance(value, Mapping):
        return value
    kind = value.get("type")
    raw = value.get("value")
    if not isinstance(kind, str) or not isinstance(raw, str):
        raise DataValidationError("invalid page cursor value")
    try:
        if kind == "decimal":
            return Decimal(raw)
        if kind == "datetime":
            return datetime.fromisoformat(raw)
        if kind == "date":
            return date.fromisoformat(raw)
        if kind == "time":
            return time.fromisoformat(raw)
        if kind == "uuid":
            return UUID(raw)
        if kind == "bytes":
            return base64.urlsafe_b64decode(raw.encode("ascii"))
    except (ValueError, TypeError) as exc:
        raise DataValidationError("invalid page cursor value") from exc
    raise DataValidationError("invalid page cursor value type")


class PageCursor:
    """Tamper-evident cursor bound to one relation and ordering contract."""

    VERSION = 1

    @classmethod
    def encode(cls, relation: QualifiedRelation, order: Sequence[OrderTerm], values: Sequence[Any]) -> str:
        order_tuple = tuple(order)
        values_tuple = tuple(values)
        if not order_tuple or len(order_tuple) != len(values_tuple):
            raise DataValidationError("page cursor ordering and values must have equal non-zero length")
        payload = {
            "version": cls.VERSION,
            "relation": [relation.schema, relation.name],
            "order": [[term.column, term.direction.value] for term in order_tuple],
            "values": [_encode_cursor_value(value) for value in values_tuple],
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        envelope = {
            "payload": payload,
            "digest": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        }
        encoded = json.dumps(envelope, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        token = base64.urlsafe_b64encode(encoded).decode("ascii").rstrip("=")
        if len(token) > MAX_CURSOR_CHARACTERS:
            raise DataValidationError("page cursor exceeds the maximum encoded size")
        return token

    @classmethod
    def decode(cls, token: str, relation: QualifiedRelation, order: Sequence[OrderTerm]) -> _CursorPayload:
        if not isinstance(token, str) or not token or len(token) > MAX_CURSOR_CHARACTERS:
            raise DataValidationError("invalid page cursor")
        try:
            padding = "=" * (-len(token) % 4)
            envelope = json.loads(base64.urlsafe_b64decode((token + padding).encode("ascii")))
            payload = envelope["payload"]
            digest = envelope["digest"]
            canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            expected_digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            if not isinstance(digest, str) or not hmac.compare_digest(expected_digest, digest):
                raise ValueError("digest")
            if payload.get("version") != cls.VERSION:
                raise ValueError("version")
            encoded_relation = payload.get("relation")
            expected_relation = [relation.schema, relation.name]
            if encoded_relation != expected_relation:
                raise DataValidationError("page cursor belongs to a different relation")
            encoded_order = payload.get("order")
            expected_order = [[term.column, term.direction.value] for term in order]
            if encoded_order != expected_order:
                raise DataValidationError("page cursor ordering does not match the request")
            raw_values = payload.get("values")
            if not isinstance(raw_values, list) or len(raw_values) != len(expected_order):
                raise ValueError("values")
            return _CursorPayload(tuple(_decode_cursor_value(value) for value in raw_values))
        except DataValidationError:
            raise
        except Exception as exc:
            raise DataValidationError("invalid page cursor") from exc


def _validate_rows(rows: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any], ...]:
    if isinstance(rows, (str, bytes, bytearray)) or any(not isinstance(row, Mapping) for row in rows):
        raise DataValidationError("rows must contain mapping values")
    normalized = tuple(dict(row) for row in rows)
    if not normalized:
        raise DataValidationError("rows must not be empty")
    if len(normalized) > MAX_DATA_ROWS:
        raise DataValidationError(f"rows cannot exceed {MAX_DATA_ROWS}")
    first_columns = tuple(normalized[0])
    _checked_columns(first_columns, label="row columns", allow_empty=False)
    first_set = set(first_columns)
    for row in normalized[1:]:
        if set(row) != first_set:
            raise DataValidationError("all rows must use the same columns")
    return normalized


def _validate_relation(value: Any) -> None:
    if not isinstance(value, QualifiedRelation):
        raise DataValidationError("relation must be a QualifiedRelation")


def _validate_filter_set(value: Any, *, label: str) -> None:
    if not isinstance(value, FilterSet):
        raise DataValidationError(f"{label} must be a FilterSet")


def _validate_guard(value: Any) -> None:
    if not isinstance(value, MutationGuard):
        raise DataValidationError("guard must be a MutationGuard")


@dataclass(frozen=True, slots=True)
class SelectRowsRequest:
    relation: QualifiedRelation
    columns: tuple[str, ...] = ()
    filters: FilterSet = FilterSet()
    order_by: tuple[OrderTerm, ...] = ()
    limit: int = 100
    cursor: str | None = None

    def __post_init__(self) -> None:
        _validate_relation(self.relation)
        _validate_filter_set(self.filters, label="filters")
        object.__setattr__(self, "columns", _checked_columns(self.columns, label="columns"))
        object.__setattr__(self, "order_by", tuple(self.order_by))
        if any(not isinstance(term, OrderTerm) for term in self.order_by):
            raise DataValidationError("order_by must contain OrderTerm values")
        if len({term.column for term in self.order_by}) != len(self.order_by):
            raise DataValidationError("order_by contains duplicate columns")
        if not isinstance(self.limit, int) or isinstance(self.limit, bool):
            raise DataValidationError("limit must be an integer")
        if self.limit <= 0 or self.limit > MAX_DATA_ROWS:
            raise DataValidationError(f"limit must be between 1 and {MAX_DATA_ROWS}")
        if self.cursor is not None and not self.order_by:
            raise DataValidationError("order_by is required when a cursor is supplied")


@dataclass(frozen=True, slots=True)
class InsertRowsRequest:
    relation: QualifiedRelation
    rows: tuple[dict[str, Any], ...]
    returning: tuple[str, ...]
    guard: MutationGuard

    def __post_init__(self) -> None:
        _validate_relation(self.relation)
        _validate_guard(self.guard)
        object.__setattr__(self, "rows", _validate_rows(self.rows))
        object.__setattr__(self, "returning", _checked_columns(self.returning, label="returning"))
        if len(self.rows) > self.guard.max_affected_rows:
            raise DataValidationError("row count cannot exceed max_affected_rows")
        if self.guard.expected_rows is not None and self.guard.expected_rows > len(self.rows):
            raise DataValidationError("expected_rows cannot exceed the submitted row count")


@dataclass(frozen=True, slots=True)
class UpsertRowsRequest:
    relation: QualifiedRelation
    rows: tuple[dict[str, Any], ...]
    conflict_columns: tuple[str, ...]
    update_columns: tuple[str, ...]
    returning: tuple[str, ...]
    guard: MutationGuard

    def __post_init__(self) -> None:
        _validate_relation(self.relation)
        _validate_guard(self.guard)
        object.__setattr__(self, "rows", _validate_rows(self.rows))
        object.__setattr__(
            self,
            "conflict_columns",
            _checked_columns(self.conflict_columns, label="conflict_columns", allow_empty=False),
        )
        object.__setattr__(self, "update_columns", _checked_columns(self.update_columns, label="update_columns"))
        object.__setattr__(self, "returning", _checked_columns(self.returning, label="returning"))
        row_columns = set(self.rows[0])
        if not set(self.conflict_columns).issubset(row_columns):
            raise DataValidationError("conflict_columns must be present in every row")
        if not set(self.update_columns).issubset(row_columns):
            raise DataValidationError("update_columns must be present in every row")
        if set(self.conflict_columns) & set(self.update_columns):
            raise DataValidationError("update_columns cannot include conflict_columns")
        if len(self.rows) > self.guard.max_affected_rows:
            raise DataValidationError("row count cannot exceed max_affected_rows")
        if self.guard.expected_rows is not None and self.guard.expected_rows > len(self.rows):
            raise DataValidationError("expected_rows cannot exceed the submitted row count")


@dataclass(frozen=True, slots=True)
class UpdateRowsRequest:
    relation: QualifiedRelation
    values: dict[str, Any]
    filters: FilterSet
    concurrency: FilterSet
    returning: tuple[str, ...]
    guard: MutationGuard

    def __post_init__(self) -> None:
        _validate_relation(self.relation)
        _validate_filter_set(self.filters, label="filters")
        _validate_filter_set(self.concurrency, label="concurrency")
        _validate_guard(self.guard)
        if not isinstance(self.values, Mapping):
            raise DataValidationError("values must be a mapping")
        values = dict(self.values)
        if not values:
            raise DataValidationError("values must not be empty")
        _checked_columns(tuple(values), label="values", allow_empty=False)
        object.__setattr__(self, "values", values)
        object.__setattr__(self, "returning", _checked_columns(self.returning, label="returning"))
        if self.filters.is_empty:
            raise DataValidationError("update requires a non-empty filter")


@dataclass(frozen=True, slots=True)
class DeleteRowsRequest:
    relation: QualifiedRelation
    filters: FilterSet
    concurrency: FilterSet
    returning: tuple[str, ...]
    guard: MutationGuard

    def __post_init__(self) -> None:
        _validate_relation(self.relation)
        _validate_filter_set(self.filters, label="filters")
        _validate_filter_set(self.concurrency, label="concurrency")
        _validate_guard(self.guard)
        object.__setattr__(self, "returning", _checked_columns(self.returning, label="returning"))
        if self.filters.is_empty:
            raise DataValidationError("delete requires a non-empty filter")


@dataclass(frozen=True, slots=True)
class RowPage:
    rows: tuple[dict[str, Any], ...]
    next_cursor: str | None
    truncated: bool
    truncation_reason: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "rows": list(self.rows),
            "next_cursor": self.next_cursor,
            "truncated": self.truncated,
            "truncation_reason": self.truncation_reason,
        }


@dataclass(frozen=True, slots=True)
class MutationResult:
    affected_rows: int
    rows: tuple[dict[str, Any], ...]

    def to_payload(self) -> dict[str, Any]:
        return {"affected_rows": self.affected_rows, "rows": list(self.rows)}
