"""Tests for bounded result serialization."""

import json
from dataclasses import dataclass
from datetime import date
from datetime import datetime
from datetime import time
from datetime import timedelta
from datetime import timezone
from decimal import Decimal
from enum import Enum
from uuid import UUID

from postgres_mcp.sql.results import BoundedQueryResult
from postgres_mcp.sql.results import ColumnInfo
from postgres_mcp.sql.results import column_info_from_description
from postgres_mcp.sql.results import encode_postgres_value
from postgres_mcp.sql.results import json_text


class Status(Enum):
    ACTIVE = "active"


class TextStatus(str, Enum):
    ACTIVE = "active"


@dataclass
class Record:
    count: int


class FakeRange:
    lower = 1
    upper = 10
    bounds = "[)"
    isempty = False


class Multirange(list[FakeRange]):
    pass


class UnknownValue:
    def __str__(self) -> str:
        return "opaque"


class Description:
    name = "amount"
    type_code = 1700
    internal_size = 16
    precision = 12
    scale = 2
    null_ok = True


def test_encode_json_native_scalars() -> None:
    assert encode_postgres_value(None) is None
    assert encode_postgres_value(True) is True
    assert encode_postgres_value("text") == "text"
    assert encode_postgres_value(42) == 42
    assert encode_postgres_value(1.25) == 1.25


def test_encode_lossy_numeric_boundaries() -> None:
    assert encode_postgres_value(2**60) == {"$pg_type": "integer", "value": str(2**60)}
    assert encode_postgres_value(Decimal("123.4500")) == {"$pg_type": "numeric", "value": "123.4500"}
    assert encode_postgres_value(float("nan"))["value"] == "NaN"
    assert encode_postgres_value(float("inf"))["value"] == "Infinity"
    assert encode_postgres_value(float("-inf"))["value"] == "-Infinity"


def test_encode_temporal_uuid_and_binary_values() -> None:
    assert encode_postgres_value(datetime(2026, 1, 2, 3, 4, 5))["$pg_type"] == "timestamp"
    assert encode_postgres_value(datetime(2026, 1, 2, tzinfo=timezone.utc))["$pg_type"] == "timestamptz"
    assert encode_postgres_value(date(2026, 1, 2)) == {"$pg_type": "date", "value": "2026-01-02"}
    assert encode_postgres_value(time(3, 4, 5))["$pg_type"] == "time"
    assert encode_postgres_value(time(3, 4, tzinfo=timezone.utc))["$pg_type"] == "timetz"
    interval = encode_postgres_value(timedelta(days=1, seconds=2))
    assert interval["$pg_type"] == "interval"
    assert interval["total_seconds"] == 86402.0
    assert encode_postgres_value(UUID("12345678-1234-5678-1234-567812345678"))["$pg_type"] == "uuid"
    assert encode_postgres_value(b"abc") == {"$pg_type": "bytea", "value": "YWJj", "encoding": "base64"}
    assert encode_postgres_value(bytearray(b"abc"))["value"] == "YWJj"
    assert encode_postgres_value(memoryview(b"abc"))["value"] == "YWJj"


def test_encode_container_enum_dataclass_range_and_unknown() -> None:
    assert encode_postgres_value(Status.ACTIVE) == {
        "$pg_type": "enum",
        "value": "active",
        "name": "ACTIVE",
    }
    assert encode_postgres_value(TextStatus.ACTIVE)["$pg_type"] == "enum"
    assert encode_postgres_value({1: [Decimal("1.1")]}) == {"1": [{"$pg_type": "numeric", "value": "1.1"}]}
    assert encode_postgres_value(Record(count=2)) == {"count": 2}
    encoded_range = encode_postgres_value(FakeRange())
    assert encoded_range == {
        "$pg_type": "range",
        "value": {"lower": 1, "upper": 10, "bounds": "[)", "empty": False},
    }
    encoded_multirange = encode_postgres_value(Multirange([FakeRange()]))
    assert encoded_multirange["$pg_type"] == "multirange"
    assert encoded_multirange["value"] == [encoded_range]
    unknown = encode_postgres_value(UnknownValue())
    assert unknown["$pg_type"].endswith("UnknownValue")
    assert unknown["value"] == "opaque"


def test_column_metadata_from_object_tuple_and_scalar() -> None:
    assert column_info_from_description(Description()) == ColumnInfo(
        name="amount",
        type_code=1700,
        internal_size=16,
        precision=12,
        scale=2,
        null_ok=True,
    )
    assert column_info_from_description(("id", 23, None, 4, 10, 0, False)) == ColumnInfo(
        name="id",
        type_code=23,
        internal_size=4,
        precision=10,
        scale=0,
        null_ok=False,
    )
    assert column_info_from_description(()) == ColumnInfo(name="")
    assert column_info_from_description("name") == ColumnInfo(name="name")


def test_column_metadata_normalizes_unportable_values() -> None:
    class OddDescription:
        name = "odd"
        type_code = object()
        internal_size = "large"
        precision = 1.2
        scale = object()
        null_ok = 1

    result = column_info_from_description(OddDescription())
    assert result.name == "odd"
    assert isinstance(result.type_code, str)
    assert result.internal_size is None
    assert result.precision is None
    assert result.scale is None
    assert result.null_ok is None


def test_bounded_result_payload_and_compact_json() -> None:
    result = BoundedQueryResult(
        rows=[{"amount": Decimal("10.50")}],
        columns=[ColumnInfo("amount", 1700)],
        row_count=1,
        truncated=True,
        affected_rows=None,
        command="SELECT",
    )
    payload = result.to_payload()
    assert payload["truncated"] is True
    assert payload["rows"][0]["amount"] == {"$pg_type": "numeric", "value": "10.50"}
    rendered = json_text(payload)
    assert " " not in rendered
    assert json.loads(rendered)["command"] == "SELECT"
