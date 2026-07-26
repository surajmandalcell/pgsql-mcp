"""MCP boundary contracts for reviewed nontransactional maintenance."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock
from unittest.mock import patch

import pytest
from mcp.types import TextContent

import postgres_mcp.server as server
from postgres_mcp.maintenance import MaintenanceExecutionError
from postgres_mcp.maintenance import MaintenanceOperation
from postgres_mcp.maintenance import MaintenanceOperationResult
from postgres_mcp.maintenance import MaintenanceOperationStatus
from postgres_mcp.maintenance import MaintenanceOptions
from postgres_mcp.maintenance import MaintenancePlanner
from postgres_mcp.maintenance import MaintenanceRequest
from postgres_mcp.maintenance import MaintenanceStatusSnapshot
from postgres_mcp.maintenance import MaintenanceTarget
from postgres_mcp.maintenance import ReconciliationResolution
from postgres_mcp.maintenance import TargetSnapshot
from postgres_mcp.runtime import AccessMode


def response_text(response: server.ResponseType) -> str:
    content = response[0]
    assert isinstance(content, TextContent)
    return content.text


def response_payload(response: server.ResponseType) -> object:
    return json.loads(response_text(response))


def request() -> MaintenanceRequest:
    return MaintenanceRequest(
        name="nightly-items-maintenance",
        operation=MaintenanceOperation.VACUUM_ANALYZE,
        target=MaintenanceTarget("app", "items"),
        options=MaintenanceOptions(skip_locked=True, index_cleanup="off", parallel=2),
    )


def plan():
    return MaintenancePlanner().create_plan(
        request(),
        TargetSnapshot(
            oid=42,
            relation_kind="r",
            persistence="p",
            is_partition=False,
            is_populated=True,
            has_usable_unique_index=False,
            is_exclusion_index=False,
        ),
    )


@pytest.mark.asyncio
async def test_create_maintenance_plan_uses_live_read_only_inspection() -> None:
    reviewed = plan()
    service = AsyncMock()
    service.plan.return_value = reviewed

    with patch.object(server, "get_maintenance_service", return_value=service):
        response = await server.create_maintenance_plan(
            name=reviewed.name,
            operation="vacuum_analyze",
            schema_name="app",
            target_name="items",
            skip_locked=True,
            index_cleanup="off",
            parallel=2,
        )

    payload = response_payload(response)
    assert isinstance(payload, dict)
    assert payload["operation"] == "vacuum_analyze"
    assert payload["transaction_behavior"] == "non_transactional"
    assert payload["rollback_available"] is False
    assert len(payload["review_hash"]) == 64
    service.plan.assert_awaited_once_with(request())


@pytest.mark.asyncio
async def test_apply_requires_unrestricted_mode_before_service_creation() -> None:
    previous = server.current_access_mode
    server.current_access_mode = AccessMode.RESTRICTED
    try:
        with patch.object(server, "get_maintenance_service") as service_factory:
            response = await server.apply_maintenance_plan(
                name="nightly-items-maintenance",
                operation="vacuum_analyze",
                schema_name="app",
                target_name="items",
                review_hash="a" * 64,
            )
        assert "requires --access-mode=unrestricted" in response_text(response)
        service_factory.assert_not_called()
    finally:
        server.current_access_mode = previous


@pytest.mark.asyncio
async def test_apply_rebuilds_live_plan_and_forwards_exact_review_hash() -> None:
    reviewed = plan()
    service = AsyncMock()
    service.plan.return_value = reviewed
    service.apply.return_value = MaintenanceOperationResult(MaintenanceOperationStatus.SUCCEEDED, None)
    previous = server.current_access_mode
    server.current_access_mode = AccessMode.UNRESTRICTED
    try:
        with patch.object(server, "get_maintenance_service", return_value=service):
            response = await server.apply_maintenance_plan(
                name=reviewed.name,
                operation="vacuum_analyze",
                schema_name="app",
                target_name="items",
                review_hash=reviewed.review_hash,
                skip_locked=True,
                index_cleanup="off",
                parallel=2,
                timeout_seconds=120,
                lock_timeout_seconds=4,
            )
    finally:
        server.current_access_mode = previous

    assert response_payload(response) == {
        "status": "succeeded",
        "record": None,
        "rollback_available": False,
    }
    service.plan.assert_awaited_once_with(request())
    service.apply.assert_awaited_once_with(
        reviewed,
        review_hash=reviewed.review_hash,
        timeout_seconds=120,
        lock_timeout_seconds=4,
    )


@pytest.mark.asyncio
async def test_maintenance_execution_error_reports_unknown_outcome_without_rollback_claim() -> None:
    reviewed = plan()
    service = AsyncMock()
    service.plan.return_value = reviewed
    service.apply.side_effect = MaintenanceExecutionError(
        "operation timed out; reconcile before retrying",
        phase="execute",
        outcome="unknown",
        error_code="timeout",
    )
    previous = server.current_access_mode
    server.current_access_mode = AccessMode.UNRESTRICTED
    try:
        with patch.object(server, "get_maintenance_service", return_value=service):
            response = await server.apply_maintenance_plan(
                name=reviewed.name,
                operation="vacuum_analyze",
                schema_name="app",
                target_name="items",
                review_hash=reviewed.review_hash,
                skip_locked=True,
                index_cleanup="off",
                parallel=2,
            )
    finally:
        server.current_access_mode = previous

    assert response_payload(response) == {
        "error": "operation timed out; reconcile before retrying",
        "phase": "execute",
        "outcome": "unknown",
        "error_code": "timeout",
        "rollback_available": False,
    }


@pytest.mark.asyncio
async def test_status_and_explicit_reconciliation_use_the_service() -> None:
    service = AsyncMock()
    service.status.return_value = MaintenanceStatusSnapshot(())
    service.reconcile.return_value = MaintenanceOperationResult(MaintenanceOperationStatus.RECONCILED_FAILED, None)

    with patch.object(server, "get_maintenance_service", return_value=service):
        status_response = await server.get_maintenance_status(limit=25)
    assert response_payload(status_response) == {"operations": []}
    service.status.assert_awaited_once_with(limit=25)

    previous = server.current_access_mode
    server.current_access_mode = AccessMode.UNRESTRICTED
    try:
        with patch.object(server, "get_maintenance_service", return_value=service):
            reconcile_response = await server.reconcile_maintenance_operation(
                name="nightly-items-maintenance",
                review_hash="b" * 64,
                resolution="failed",
            )
    finally:
        server.current_access_mode = previous

    assert response_payload(reconcile_response) == {
        "status": "reconciled_failed",
        "record": None,
        "rollback_available": False,
    }
    service.reconcile.assert_awaited_once_with(
        name="nightly-items-maintenance",
        review_hash="b" * 64,
        resolution=ReconciliationResolution.FAILED,
    )


@pytest.mark.asyncio
async def test_reconciliation_requires_unrestricted_mode() -> None:
    previous = server.current_access_mode
    server.current_access_mode = AccessMode.RESTRICTED
    try:
        with patch.object(server, "get_maintenance_service") as service_factory:
            response = await server.reconcile_maintenance_operation(
                name="nightly-items-maintenance",
                review_hash="b" * 64,
                resolution="failed",
            )
        assert "requires --access-mode=unrestricted" in response_text(response)
        service_factory.assert_not_called()
    finally:
        server.current_access_mode = previous


@pytest.mark.asyncio
async def test_capabilities_advertise_reviewed_nontransactional_maintenance() -> None:
    payload = response_payload(await server.get_server_capabilities())
    assert isinstance(payload, dict)
    assert payload["maintenance"]["planning"] is True
    assert payload["maintenance"]["apply_available"] is False
    assert payload["maintenance"]["review_hash_required"] is True
    assert payload["maintenance"]["rollback_available"] is False
    assert payload["maintenance"]["unknown_outcome_reconciliation"] is True


def test_cli_exposes_a_separate_trusted_maintenance_ledger_schema() -> None:
    parser = server.build_argument_parser()
    args = parser.parse_args(["postgresql://localhost/app", "--maintenance-schema", "platform"])

    assert args.maintenance_schema == "platform"
    assert args.access_mode == "restricted"
