"""Tests for safe server defaults and bounded public execution tools."""

import json
import subprocess
import sys
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

import postgres_mcp.server as server
from postgres_mcp.runtime import ABSOLUTE_MAX_ROWS
from postgres_mcp.runtime import AccessMode
from postgres_mcp.runtime import ServerProfile
from postgres_mcp.sql.results import BoundedQueryResult
from postgres_mcp.sql.transaction import IsolationLevel
from postgres_mcp.sql.transaction import TransactionExecutionError
from postgres_mcp.sql.transaction import TransactionExecutionResult


def response_payload(response: server.ResponseType) -> object:
    return json.loads(response[0].text)


def test_server_defaults_are_restricted_and_full_profile() -> None:
    assert server.current_access_mode is AccessMode.RESTRICTED
    assert server.current_profile is ServerProfile.FULL
    parser = server.build_argument_parser()
    args = parser.parse_args(["postgresql://localhost/database"])
    assert args.access_mode == AccessMode.RESTRICTED.value


@pytest.mark.asyncio
async def test_capabilities_report_effective_policy() -> None:
    with (
        patch.object(server, "current_access_mode", AccessMode.RESTRICTED),
        patch.object(server, "current_max_rows", 25),
        patch.object(server, "current_query_timeout", 4.5),
    ):
        payload = response_payload(await server.get_server_capabilities())

    assert isinstance(payload, dict)
    assert payload["profile"] == "full"
    assert payload["access_mode"] == "restricted"
    assert payload["query"]["single_statement"] is True
    assert payload["query"]["raw_sql_writes"] is False
    assert payload["query"]["default_max_rows"] == 25
    assert payload["transactions"]["available"] is False


@pytest.mark.asyncio
async def test_restricted_execute_sql_uses_safe_bounded_executor() -> None:
    expected = BoundedQueryResult(
        rows=[{"value": 1}],
        columns=[],
        row_count=1,
        truncated=False,
        affected_rows=None,
        command="SELECT",
    )
    executor = MagicMock()
    executor.execute_bounded_query = AsyncMock(return_value=expected)
    base_driver = MagicMock()

    with (
        patch.object(server, "current_access_mode", AccessMode.RESTRICTED),
        patch.object(server, "current_max_rows", 20),
        patch.object(server, "current_query_timeout", 3),
        patch.object(server, "get_base_sql_driver", return_value=base_driver),
        patch.object(server, "SafeQueryExecutor", return_value=executor) as executor_type,
    ):
        response = await server.execute_sql("SELECT %s::integer AS value", params=[1], max_rows=5)

    payload = response_payload(response)
    assert isinstance(payload, dict)
    assert payload["rows"] == [{"value": 1}]
    executor_type.assert_called_once_with(base_driver, timeout_seconds=3)
    executor.execute_bounded_query.assert_awaited_once_with(
        "SELECT %s::integer AS value",
        params=[1],
        max_rows=5,
    )


@pytest.mark.asyncio
async def test_unrestricted_mode_keeps_raw_sql_read_only_and_bounds_results() -> None:
    expected = BoundedQueryResult(
        rows=[{"value": 1}],
        columns=[],
        row_count=1,
        truncated=False,
        affected_rows=None,
        command="SELECT",
    )
    executor = MagicMock()
    executor.execute_bounded_query = AsyncMock(return_value=expected)
    base_driver = MagicMock()

    with (
        patch.object(server, "current_access_mode", AccessMode.UNRESTRICTED),
        patch.object(server, "current_max_rows", 20),
        patch.object(server, "current_query_timeout", 3),
        patch.object(server, "get_base_sql_driver", return_value=base_driver),
        patch.object(server, "SafeQueryExecutor", return_value=executor),
    ):
        response = await server.execute_sql("SELECT 1 AS value", max_rows=2)

    payload = response_payload(response)
    assert isinstance(payload, dict)
    assert payload["rows"] == [{"value": 1}]
    executor.execute_bounded_query.assert_awaited_once_with(
        "SELECT 1 AS value",
        params=None,
        max_rows=2,
    )

    with (
        patch.object(server, "current_access_mode", AccessMode.UNRESTRICTED),
        patch.object(server, "get_base_sql_driver", return_value=base_driver),
    ):
        write_response = await server.execute_sql(
            "UPDATE public.items SET value = %s WHERE id = %s",
            params=["new", 1],
        )
        multiple_response = await server.execute_sql("SELECT 1; SELECT 2")

    assert "statement type 'update'" in write_response[0].text
    assert multiple_response[0].text.startswith("Error: exactly one SQL statement")


@pytest.mark.asyncio
async def test_execute_sql_rejects_parameter_and_limit_errors_before_database_work() -> None:
    base_driver = MagicMock()
    base_driver.execute_bounded_query = AsyncMock()
    with (
        patch.object(server, "current_access_mode", AccessMode.UNRESTRICTED),
        patch.object(server, "get_base_sql_driver", return_value=base_driver),
    ):
        parameter_response = await server.execute_sql("SELECT %s", params=[])
        limit_response = await server.execute_sql("SELECT 1", max_rows=ABSOLUTE_MAX_ROWS + 1)

    assert "positional placeholders" in parameter_response[0].text
    assert "cannot exceed" in limit_response[0].text
    base_driver.execute_bounded_query.assert_not_awaited()


@pytest.mark.asyncio
async def test_atomic_transaction_is_unavailable_in_restricted_mode() -> None:
    step = server.TransactionStepInput(
        sql="UPDATE public.items SET value = 1 WHERE id = 1",
        max_affected_rows=1,
    )
    with patch.object(server, "current_access_mode", AccessMode.RESTRICTED):
        response = await server.execute_transaction([step])
    assert "require --access-mode=unrestricted" in response[0].text


@pytest.mark.asyncio
async def test_atomic_transaction_maps_models_and_returns_commit_payload() -> None:
    result = TransactionExecutionResult(
        committed=True,
        isolation=IsolationLevel.SERIALIZABLE,
        read_only=False,
        steps=[],
    )
    base_driver = MagicMock()
    base_driver.execute_transaction = AsyncMock(return_value=result)
    step = server.TransactionStepInput(
        sql="UPDATE public.items SET value = %s WHERE id = %s",
        params=["new", 1],
        expected_rows=1,
        max_affected_rows=1,
        result_mode="rows",
        max_rows=5,
    )

    with (
        patch.object(server, "current_access_mode", AccessMode.UNRESTRICTED),
        patch.object(server, "get_base_sql_driver", return_value=base_driver),
    ):
        response = await server.execute_transaction(
            [step],
            isolation="serializable",
            timeout_seconds=10,
            lock_timeout_seconds=2,
        )

    payload = response_payload(response)
    assert isinstance(payload, dict)
    assert payload["committed"] is True
    call = base_driver.execute_transaction.await_args
    assert call is not None
    transaction_step = call.args[0][0]
    assert transaction_step.params == ("new", 1)
    assert transaction_step.expected_rows == 1
    assert transaction_step.max_affected_rows == 1
    assert call.kwargs["isolation"] is IsolationLevel.SERIALIZABLE


@pytest.mark.asyncio
async def test_atomic_transaction_returns_explicit_rollback_payload() -> None:
    base_driver = MagicMock()
    base_driver.execute_transaction = AsyncMock(side_effect=TransactionExecutionError("row guard failed", failed_step=0))
    step = server.TransactionStepInput(
        sql="DELETE FROM public.items WHERE id = 1",
        max_affected_rows=1,
    )

    with (
        patch.object(server, "current_access_mode", AccessMode.UNRESTRICTED),
        patch.object(server, "get_base_sql_driver", return_value=base_driver),
    ):
        response = await server.execute_transaction([step])

    payload = response_payload(response)
    assert payload == {
        "committed": False,
        "rolled_back": True,
        "failed_step": 0,
        "error": "row guard failed",
    }


def test_full_server_import_does_not_load_llm_optimizer() -> None:
    code = "import sys; import postgres_mcp.server; assert 'postgres_mcp.index.llm_opt' not in sys.modules"
    completed = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
