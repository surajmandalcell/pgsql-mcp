"""Bounded query results and PostgreSQL-safe JSON serialization."""

from __future__ import annotations

import base64
import dataclasses
import json
import math
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from datetime import datetime
from datetime import time
from datetime import timedelta
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

_JSON_SAFE_INTEGER = 2**53 - 1


def _tag(pg_type: str, value: Any, **metadata: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"$pg_type": pg_type, "value": value}
    payload.update(metadata)
    return payload


def encode_postgres_value(value: Any) -> Any:
    """Encode PostgreSQL values without silently losing precision or type intent.

    Known scalar families use compact tagged values when ordinary JSON would be
    lossy. Unknown extension and user-defined values retain a qualified Python
    type name and their canonical string representation instead of failing the
    whole response.
    """
    if value is None:
        return None
    if isinstance(value, Enum):
        return _tag("enum", encode_postgres_value(value.value), name=value.name)
    if isinstance(value, (bool, str)):
        return value
    if isinstance(value, int):
        if abs(value) <= _JSON_SAFE_INTEGER:
            return value
        return _tag("integer", str(value))
    if isinstance(value, float):
        if math.isfinite(value):
            return value
        if math.isnan(value):
            rendered = "NaN"
        elif value > 0:
            rendered = "Infinity"
        else:
            rendered = "-Infinity"
        return _tag("float8", rendered)
    if isinstance(value, Decimal):
        return _tag("numeric", str(value))
    if isinstance(value, datetime):
        return _tag("timestamptz" if value.tzinfo is not None else "timestamp", value.isoformat())
    if isinstance(value, date):
        return _tag("date", value.isoformat())
    if isinstance(value, time):
        return _tag("timetz" if value.tzinfo is not None else "time", value.isoformat())
    if isinstance(value, timedelta):
        return _tag("interval", str(value), total_seconds=value.total_seconds())
    if isinstance(value, UUID):
        return _tag("uuid", str(value))
    if isinstance(value, (bytes, bytearray, memoryview)):
        encoded = base64.b64encode(bytes(value)).decode("ascii")
        return _tag("bytea", encoded, encoding="base64")
    if isinstance(value, Mapping):
        return {str(key): encode_postgres_value(item) for key, item in value.items()}
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return encode_postgres_value(dataclasses.asdict(value))

    # Psycopg range values expose these attributes. Attribute checks avoid
    # importing adapter classes and keep the core startup path light.
    if all(hasattr(value, attribute) for attribute in ("lower", "upper", "bounds", "isempty")):
        return _tag(
            "range",
            {
                "lower": encode_postgres_value(value.lower),
                "upper": encode_postgres_value(value.upper),
                "bounds": value.bounds,
                "empty": bool(value.isempty),
            },
        )

    qualified_name = f"{type(value).__module__}.{type(value).__qualname__}"
    if type(value).__qualname__.endswith("Multirange") and isinstance(value, Sequence):
        return _tag("multirange", [encode_postgres_value(item) for item in value])
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray, memoryview)):
        return [encode_postgres_value(item) for item in value]
    return _tag(qualified_name, str(value))


@dataclass(frozen=True, slots=True)
class ColumnInfo:
    """Portable subset of DB-API column metadata."""

    name: str
    type_code: int | str | None = None
    internal_size: int | None = None
    precision: int | None = None
    scale: int | None = None
    null_ok: bool | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type_code": self.type_code,
            "internal_size": self.internal_size,
            "precision": self.precision,
            "scale": self.scale,
            "null_ok": self.null_ok,
        }


@dataclass(frozen=True, slots=True)
class BoundedQueryResult:
    """A result that cannot grow beyond the caller's validated row budget."""

    rows: list[dict[str, Any]]
    columns: list[ColumnInfo]
    row_count: int
    truncated: bool
    affected_rows: int | None
    command: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "columns": [column.to_payload() for column in self.columns],
            "rows": encode_postgres_value(self.rows),
            "row_count": self.row_count,
            "affected_rows": self.affected_rows,
            "truncated": self.truncated,
        }


def column_info_from_description(description: Any) -> ColumnInfo:
    """Convert psycopg or DB-API description entries into stable metadata."""
    if hasattr(description, "name"):
        name = str(description.name)
        type_code = getattr(description, "type_code", None)
        internal_size = getattr(description, "internal_size", None)
        precision = getattr(description, "precision", None)
        scale = getattr(description, "scale", None)
        null_ok = getattr(description, "null_ok", None)
    elif isinstance(description, Sequence) and not isinstance(description, (str, bytes, bytearray)):
        values = list(description)
        name = str(values[0]) if values else ""
        type_code = values[1] if len(values) > 1 else None
        internal_size = values[3] if len(values) > 3 else None
        precision = values[4] if len(values) > 4 else None
        scale = values[5] if len(values) > 5 else None
        null_ok = values[6] if len(values) > 6 else None
    else:
        name = str(description)
        type_code = internal_size = precision = scale = null_ok = None

    if type_code is not None and not isinstance(type_code, (int, str)):
        type_code = str(type_code)
    return ColumnInfo(
        name=name,
        type_code=type_code,
        internal_size=internal_size if isinstance(internal_size, int) else None,
        precision=precision if isinstance(precision, int) else None,
        scale=scale if isinstance(scale, int) else None,
        null_ok=null_ok if isinstance(null_ok, bool) else None,
    )


def json_text(value: Any) -> str:
    """Serialize a response compactly for MCP text content."""
    return json.dumps(encode_postgres_value(value), ensure_ascii=False, separators=(",", ":"))
