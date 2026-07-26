"""Contract tests for the deliberately small pgsql-mcp-lite profile."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from unittest.mock import AsyncMock
from unittest.mock import patch

import pytest
from mcp.types import TextContent

import postgres_mcp.lite_server as lite
from postgres_mcp.sql.results import BoundedQueryResult


def response_text(response: lite.ResponseType) -> str:
    """Extract text while preserving the declared MCP response union."""
    content = response[0]
    assert isinstance(content, TextContent)
    return content.text


def response_payload(response: lite.ResponseType) -> object:
    """Decode one structured lite response."""
    return json.loads(response_text(response))


def test_package_import_does_not_eagerly_load_servers() -> None:
    """Importing the distribution must not load either server implementation."""
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(filter(None, ["src", environment.get("PYTHONPATH")]))
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import postgres_mcp; "
                "assert 'postgres_mcp.server' not in sys.modules; "
                "assert 'postgres_mcp.lite_server' not in sys.modules; assert 'postgres_mcp.ha_server' not in sys.modules; "
                "assert 'postgres_mcp.top_queries' not in sys.modules"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert completed.returncode == 0, completed.stderr


@pytest.mark.asyncio
async def test_lite_capabilities_are_small_and_read_only() -> None:
    """The advertised profile must match the actual immutable lite policy."""
    payload = response_payload(await lite.get_server_capabilities())
    assert isinstance(payload, dict)
    assert payload["profile"] == "lite"
    assert payload["read_only"] is True
    assert payload["transactions"] is False
    assert payload["absolute_max_rows"] == 500
    assert payload["pool"] == {"min_size": 0, "max_size": 2}
    assert payload["tools"] == [
        "get_server_capabilities",
        "list_schemas",
        "list_objects",
        "get_object_details",
        "execute_sql",
        "explain_query",
    ]
    assert "llm_features" in payload["omitted"]
    assert "maintenance" in payload["omitted"]
    assert "replication" in payload["omitted"]


def test_lite_cli_has_no_write_mode() -> None:
    """The lite executable must not expose an access-mode escape hatch."""
    parser = lite.build_argument_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--access-mode=unrestricted"])


@pytest.mark.asyncio
async def test_lite_execute_sql_uses_shared_bounded_safety_kernel() -> None:
    """Lite queries must retain native parameters and the lower hard row limit."""
    result = BoundedQueryResult(
        rows=[{"value": 7}],
        columns=[],
        row_count=1,
        truncated=False,
        affected_rows=None,
        command="SELECT",
    )
    executor = AsyncMock()
    executor.execute_bounded_query.return_value = result

    with patch.object(lite, "SafeQueryExecutor", return_value=executor):
        response = await lite.execute_sql("SELECT %s::integer AS value", params=[7], max_rows=5)

    payload = response_payload(response)
    assert isinstance(payload, dict)
    assert payload["rows"] == [{"value": 7}]
    executor.execute_bounded_query.assert_awaited_once_with(
        "SELECT %s::integer AS value",
        params=[7],
        max_rows=5,
    )


@pytest.mark.asyncio
async def test_lite_execute_sql_rejects_limits_above_profile_ceiling() -> None:
    """A caller cannot expand lite into a high-context result channel."""
    response = await lite.execute_sql("SELECT 1", max_rows=501)
    assert "max_rows cannot exceed 500" in response_text(response)


@pytest.mark.asyncio
async def test_lite_explain_is_validation_only_and_never_analyze() -> None:
    """The lite plan tool validates once and only invokes non-executing EXPLAIN."""
    executor = AsyncMock()
    explain_tool = AsyncMock()
    explain_tool.explain.return_value = lite.ErrorResult("planned")

    with (
        patch.object(lite, "SafeQueryExecutor", return_value=executor),
        patch.object(lite, "ExplainPlanTool", return_value=explain_tool),
        patch.object(lite, "get_readonly_sql_driver", new=AsyncMock(return_value=AsyncMock())),
    ):
        response = await lite.explain_query("SELECT 1")

    executor.validate_query.assert_awaited_once_with("SELECT 1", parameter_count=0)
    explain_tool.explain.assert_awaited_once_with("SELECT 1")
    assert "planned" in response_text(response)


def test_lite_pool_is_lazy_and_small() -> None:
    """The profile must not keep idle connections or permit broad fan-out."""
    assert lite.db_connection.min_size == 0
    assert lite.db_connection.max_size == 2


def test_lite_server_import_does_not_load_maintenance_domain() -> None:
    """The lite entry point must not import the operational maintenance stack."""
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(filter(None, ["src", environment.get("PYTHONPATH")]))
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import postgres_mcp.lite_server; "
                "assert 'postgres_mcp.maintenance' not in sys.modules; "
                "assert 'postgres_mcp.maintenance.postgres' not in sys.modules"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert completed.returncode == 0, completed.stderr
