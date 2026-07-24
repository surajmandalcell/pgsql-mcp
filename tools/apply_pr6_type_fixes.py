"""Apply the exact type-safety fixes identified by PR #6 CI.

This helper is temporary and is deleted after the fixes are committed. Keeping
its transformations explicit makes the CI-assisted repair auditable.
"""

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    content = target.read_text()
    if content.count(old) != 1:
        raise RuntimeError(f"Expected exactly one match in {path!r}")
    target.write_text(content.replace(old, new, 1))


replace_once(
    "src/postgres_mcp/sql/results.py",
    """                "lower": encode_postgres_value(value.lower),
                "upper": encode_postgres_value(value.upper),
                "bounds": value.bounds,
                "empty": bool(value.isempty),
""",
    """                "lower": encode_postgres_value(getattr(value, "lower")),
                "upper": encode_postgres_value(getattr(value, "upper")),
                "bounds": getattr(value, "bounds"),
                "empty": bool(getattr(value, "isempty")),
""",
)

replace_once(
    "src/postgres_mcp/sql/sql_driver.py",
    """    @property
    def is_valid(self) -> bool:
        return self._is_valid
""",
    '''    def mark_invalid(self, error: BaseException) -> None:
        """Record a connection-level failure without exposing mutable internals."""
        self._is_valid = False
        self._last_error = str(error)

    @property
    def is_valid(self) -> bool:
        return self._is_valid
''',
)
replace_once(
    "src/postgres_mcp/sql/sql_driver.py",
    """        if self.conn is not None and self.is_pool:
            self.conn._is_valid = False
            self.conn._last_error = str(root_error)
        elif self.conn is not None:
""",
    """        if self.conn is not None and self.is_pool:
            self.conn.mark_invalid(root_error)
        elif self.conn is not None:
""",
)

replace_once(
    "src/postgres_mcp/server.py",
    """from .sql import parse_single_statement  # noqa: E402
from .transport import env_number  # noqa: E402
""",
    """from .sql import parse_single_statement  # noqa: E402
from .transport import DEFAULT_SSE_HOST as DEFAULT_SSE_HOST  # noqa: E402
from .transport import DEFAULT_SSE_PATH as DEFAULT_SSE_PATH  # noqa: E402
from .transport import DEFAULT_SSE_PORT as DEFAULT_SSE_PORT  # noqa: E402
from .transport import env_number  # noqa: E402
""",
)

replace_once(
    "tests/unit/server/test_safety_foundation.py",
    """import pytest

import postgres_mcp.server as server
""",
    """import pytest
from mcp.types import TextContent

import postgres_mcp.server as server
""",
)
replace_once(
    "tests/unit/server/test_safety_foundation.py",
    """def response_payload(response: server.ResponseType) -> object:
    return json.loads(response[0].text)
""",
    """def response_text(response: server.ResponseType) -> str:
    content = response[0]
    assert isinstance(content, TextContent)
    return content.text


def response_payload(response: server.ResponseType) -> object:
    return json.loads(response_text(response))
""",
)

safety_test = Path("tests/unit/server/test_safety_foundation.py")
safety_content = safety_test.read_text()
replacements = {
    "write_response[0].text": "response_text(write_response)",
    "multiple_response[0].text": "response_text(multiple_response)",
    "parameter_response[0].text": "response_text(parameter_response)",
    "limit_response[0].text": "response_text(limit_response)",
    "response[0].text": "response_text(response)",
}
for old, new in replacements.items():
    safety_content = safety_content.replace(old, new)
safety_test.write_text(safety_content)
