"""Integration-style tests for the server's EXPLAIN tool wiring."""

from typing import Any
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from postgres_mcp.artifacts import ExplainPlanArtifact
from postgres_mcp.runtime import AccessMode
from postgres_mcp.server import HypotheticalIndex
from postgres_mcp.server import explain_query


def plan_artifact(*, node_type: str = "Seq Scan", execution_time: float | None = None) -> ExplainPlanArtifact:
    payload: dict[str, Any] = {
        "Plan": {
            "Node Type": node_type,
            "Total Cost": 10.0,
            "Startup Cost": 0.0,
            "Plan Rows": 1,
            "Plan Width": 8,
        }
    }
    if execution_time is not None:
        payload["Execution Time"] = execution_time
    return ExplainPlanArtifact.from_json_data(payload)


@pytest.mark.asyncio
async def test_explain_query_integration() -> None:
    driver = MagicMock()
    tool = MagicMock()
    tool.explain = AsyncMock(return_value=plan_artifact())

    with (
        patch("postgres_mcp.server.get_sql_driver", AsyncMock(return_value=driver)),
        patch("postgres_mcp.server.ExplainPlanTool", return_value=tool),
    ):
        result = await explain_query("SELECT * FROM users", hypothetical_indexes=None)

    assert "Seq Scan" in result[0].text
    tool.explain.assert_awaited_once_with("SELECT * FROM users")


@pytest.mark.asyncio
async def test_explain_query_with_analyze_integration() -> None:
    """EXPLAIN ANALYZE remains available only when writes are explicitly enabled."""
    driver = MagicMock()
    tool = MagicMock()
    tool.explain_analyze = AsyncMock(return_value=plan_artifact(execution_time=1.23))

    with (
        patch("postgres_mcp.server.current_access_mode", AccessMode.UNRESTRICTED),
        patch("postgres_mcp.server.get_sql_driver", AsyncMock(return_value=driver)),
        patch("postgres_mcp.server.ExplainPlanTool", return_value=tool),
    ):
        result = await explain_query("SELECT * FROM users", analyze=True, hypothetical_indexes=None)

    assert "Execution Time: 1.230 ms" in result[0].text
    tool.explain_analyze.assert_awaited_once_with("SELECT * FROM users")


@pytest.mark.asyncio
async def test_explain_analyze_is_blocked_in_restricted_mode() -> None:
    with patch("postgres_mcp.server.current_access_mode", AccessMode.RESTRICTED):
        result = await explain_query("SELECT * FROM users", analyze=True)
    assert "disabled in restricted mode" in result[0].text


@pytest.mark.asyncio
async def test_explain_query_with_hypothetical_indexes_integration() -> None:
    driver = MagicMock()
    tool = MagicMock()
    tool.explain_with_hypothetical_indexes = AsyncMock(return_value=plan_artifact(node_type="Index Scan"))
    index = HypotheticalIndex(table="users", columns=["email"])

    with (
        patch("postgres_mcp.server.get_sql_driver", AsyncMock(return_value=driver)),
        patch("postgres_mcp.server.check_hypopg_installation_status", AsyncMock(return_value=(True, "installed"))),
        patch("postgres_mcp.server.ExplainPlanTool", return_value=tool),
    ):
        result = await explain_query(
            "SELECT * FROM users WHERE email = 'test@example.com'",
            hypothetical_indexes=[index],
        )

    assert "Index Scan" in result[0].text
    tool.explain_with_hypothetical_indexes.assert_awaited_once_with(
        "SELECT * FROM users WHERE email = 'test@example.com'",
        [{"table": "users", "columns": ["email"], "using": "btree"}],
    )


@pytest.mark.asyncio
async def test_explain_query_missing_hypopg_integration() -> None:
    driver = MagicMock()
    index = HypotheticalIndex(table="users", columns=["email"])
    with (
        patch("postgres_mcp.server.get_sql_driver", AsyncMock(return_value=driver)),
        patch(
            "postgres_mcp.server.check_hypopg_installation_status",
            AsyncMock(return_value=(False, "extension is required")),
        ),
    ):
        result = await explain_query("SELECT * FROM users", hypothetical_indexes=[index])
    assert result[0].text == "extension is required"


@pytest.mark.asyncio
async def test_explain_query_error_handling_integration() -> None:
    with patch(
        "postgres_mcp.server.get_sql_driver",
        AsyncMock(side_effect=RuntimeError("Error executing query")),
    ):
        result = await explain_query("INVALID SQL")
    assert "Error executing query" in result[0].text
