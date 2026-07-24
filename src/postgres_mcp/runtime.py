"""Runtime configuration shared by the full and lite MCP servers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

DEFAULT_QUERY_TIMEOUT_SECONDS = 30.0
DEFAULT_LOCK_TIMEOUT_SECONDS = 5.0
DEFAULT_MAX_ROWS = 100
ABSOLUTE_MAX_ROWS = 5_000
LITE_ABSOLUTE_MAX_ROWS = 500
DEFAULT_POOL_MIN_SIZE = 0
DEFAULT_POOL_MAX_SIZE = 5


class AccessMode(str, Enum):
    """Database access policy exposed by the MCP server."""

    RESTRICTED = "restricted"
    UNRESTRICTED = "unrestricted"


class ServerProfile(str, Enum):
    """Feature profiles shipped by pgsql-mcp."""

    FULL = "full"
    LITE = "lite"


@dataclass(frozen=True, slots=True)
class QueryLimits:
    """Validated runtime limits for query and transaction execution."""

    timeout_seconds: float = DEFAULT_QUERY_TIMEOUT_SECONDS
    lock_timeout_seconds: float = DEFAULT_LOCK_TIMEOUT_SECONDS
    default_max_rows: int = DEFAULT_MAX_ROWS
    absolute_max_rows: int = ABSOLUTE_MAX_ROWS

    def validate(self) -> None:
        """Raise when a configured limit is unsafe or internally inconsistent."""
        if self.timeout_seconds <= 0:
            raise ValueError("query timeout must be greater than zero")
        if self.lock_timeout_seconds <= 0:
            raise ValueError("lock timeout must be greater than zero")
        if self.default_max_rows <= 0:
            raise ValueError("default row limit must be greater than zero")
        if self.absolute_max_rows < self.default_max_rows:
            raise ValueError("absolute row limit must be greater than or equal to the default")

    def checked_row_limit(self, requested: int | None) -> int:
        """Return a bounded row limit suitable for database execution."""
        row_limit = self.default_max_rows if requested is None else requested
        if row_limit <= 0:
            raise ValueError("max_rows must be greater than zero")
        if row_limit > self.absolute_max_rows:
            raise ValueError(f"max_rows cannot exceed {self.absolute_max_rows}")
        return row_limit
