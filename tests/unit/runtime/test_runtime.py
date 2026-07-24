"""Tests for shared runtime limits and profiles."""

import pytest

from postgres_mcp.runtime import ABSOLUTE_MAX_ROWS
from postgres_mcp.runtime import DEFAULT_MAX_ROWS
from postgres_mcp.runtime import AccessMode
from postgres_mcp.runtime import QueryLimits
from postgres_mcp.runtime import ServerProfile


def test_runtime_enum_values() -> None:
    assert AccessMode.RESTRICTED.value == "restricted"
    assert AccessMode.UNRESTRICTED.value == "unrestricted"
    assert ServerProfile.FULL.value == "full"
    assert ServerProfile.LITE.value == "lite"


def test_query_limits_defaults_and_checked_rows() -> None:
    limits = QueryLimits()
    limits.validate()
    assert limits.checked_row_limit(None) == DEFAULT_MAX_ROWS
    assert limits.checked_row_limit(1) == 1
    assert limits.checked_row_limit(ABSOLUTE_MAX_ROWS) == ABSOLUTE_MAX_ROWS


@pytest.mark.parametrize(
    ("limits", "message"),
    [
        (QueryLimits(timeout_seconds=0), "query timeout"),
        (QueryLimits(lock_timeout_seconds=0), "lock timeout"),
        (QueryLimits(default_max_rows=0), "default row limit"),
        (QueryLimits(default_max_rows=10, absolute_max_rows=9), "absolute row limit"),
    ],
)
def test_query_limits_reject_invalid_configuration(limits: QueryLimits, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        limits.validate()


@pytest.mark.parametrize("requested", [0, -1])
def test_checked_rows_reject_non_positive_values(requested: int) -> None:
    with pytest.raises(ValueError, match="greater than zero"):
        QueryLimits().checked_row_limit(requested)


def test_checked_rows_reject_values_above_absolute_limit() -> None:
    limits = QueryLimits(default_max_rows=10, absolute_max_rows=20)
    with pytest.raises(ValueError, match="cannot exceed 20"):
        limits.checked_row_limit(21)
