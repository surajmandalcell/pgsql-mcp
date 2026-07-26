"""Error-boundary contracts for reviewed-maintenance MCP tools."""

from __future__ import annotations

from unittest.mock import AsyncMock
from unittest.mock import patch

import pytest
from mcp.types import TextContent

import postgres_mcp.server as server
from postgres_mcp.maintenance import MaintenanceExecutionError
from postgres_mcp.maintenance import MaintenanceValidationError
from postgres_mcp.runtime import AccessMode


def response_text(response: server.ResponseType) -> str:
    content = response[0]
    assert isinstance(content, TextContent)
    return content.text


def test_maintenance_service_factory_uses_bounded_runtime_configuration() -> None:
    driver = object()
    repository = object()
    service = object()
    previous_schema = server.current_maintenance_schema
    previous_timeout = server.current_query_timeout
    server.current_maintenance_schema = "platform"
    server.current_query_timeout = 0.25
    try:
        with (
            patch.object(server, "get_base_sql_driver", return_value=driver),
            patch.object(server, "PostgresMaintenanceBackend", return_value=repository) as repository_factory,
            patch.object(server, "MaintenanceService", return_value=service) as service_factory,
        ):
            assert server.get_maintenance_service() is service
    finally:
        server.current_maintenance_schema = previous_schema
        server.current_query_timeout = previous_timeout

    repository_factory.assert_called_once_with(
        driver,
        ledger_schema="platform",
        inspection_timeout_seconds=1,
    )
    service_factory.assert_called_once_with(repository)


@pytest.mark.asyncio
@pytest.mark.parametrize("unexpected", [False, True])
async def test_create_plan_formats_expected_and_unexpected_errors(unexpected: bool) -> None:
    service = AsyncMock()
    service.plan.side_effect = RuntimeError("unexpected") if unexpected else MaintenanceValidationError("invalid request")
    with patch.object(server, "get_maintenance_service", return_value=service):
        response = await server.create_maintenance_plan(
            name="maintenance",
            operation="vacuum_analyze",
            schema_name="app",
            target_name="items",
        )
    assert response_text(response) == ("Error: unexpected" if unexpected else "Error: invalid request")


@pytest.mark.asyncio
@pytest.mark.parametrize("unexpected", [False, True])
async def test_apply_formats_domain_and_unexpected_errors(unexpected: bool) -> None:
    service = AsyncMock()
    service.plan.side_effect = RuntimeError("unexpected") if unexpected else MaintenanceValidationError("invalid plan")
    previous = server.current_access_mode
    server.current_access_mode = AccessMode.UNRESTRICTED
    try:
        with patch.object(server, "get_maintenance_service", return_value=service):
            response = await server.apply_maintenance_plan(
                name="maintenance",
                operation="vacuum_analyze",
                schema_name="app",
                target_name="items",
                review_hash="a" * 64,
            )
    finally:
        server.current_access_mode = previous
    assert response_text(response) == ("Error: unexpected" if unexpected else "Error: invalid plan")


@pytest.mark.asyncio
@pytest.mark.parametrize("unexpected", [False, True])
async def test_status_formats_domain_and_unexpected_errors(unexpected: bool) -> None:
    service = AsyncMock()
    service.status.side_effect = RuntimeError("unexpected") if unexpected else MaintenanceValidationError("invalid status")
    with patch.object(server, "get_maintenance_service", return_value=service):
        response = await server.get_maintenance_status(limit=10)
    assert response_text(response) == ("Error: unexpected" if unexpected else "Error: invalid status")


@pytest.mark.asyncio
async def test_reconcile_formats_execution_domain_and_unexpected_errors() -> None:
    previous = server.current_access_mode
    server.current_access_mode = AccessMode.UNRESTRICTED
    try:
        cases = [
            (
                MaintenanceExecutionError(
                    "unknown outcome",
                    phase="reconcile",
                    outcome="unknown",
                    error_code="timeout",
                ),
                '"rollback_available":false',
            ),
            (MaintenanceValidationError("invalid reconciliation"), "Error: invalid reconciliation"),
            (RuntimeError("unexpected"), "Error: unexpected"),
        ]
        for error, expected in cases:
            service = AsyncMock()
            service.reconcile.side_effect = error
            with patch.object(server, "get_maintenance_service", return_value=service):
                response = await server.reconcile_maintenance_operation(
                    name="maintenance",
                    review_hash="a" * 64,
                    resolution="failed",
                )
            assert expected in response_text(response)
    finally:
        server.current_access_mode = previous
