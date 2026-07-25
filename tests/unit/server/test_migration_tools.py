"""MCP boundary contracts for the reviewed migration bounded context."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock
from unittest.mock import patch

import pytest
from mcp.types import TextContent

import postgres_mcp.server as server
from postgres_mcp.migrations import MigrationConflictError
from postgres_mcp.migrations import MigrationExecutionError
from postgres_mcp.migrations import MigrationOperationResult
from postgres_mcp.migrations import MigrationOperationStatus
from postgres_mcp.migrations import MigrationPlanner
from postgres_mcp.migrations import MigrationStatusSnapshot
from postgres_mcp.migrations import MigrationStepDraft
from postgres_mcp.runtime import AccessMode


def response_text(response: server.ResponseType) -> str:
    content = response[0]
    assert isinstance(content, TextContent)
    return content.text


def response_payload(response: server.ResponseType) -> object:
    return json.loads(response_text(response))


def migration_input() -> list[server.MigrationStepInput]:
    return [server.MigrationStepInput(sql="CREATE TABLE app.items(id integer)", rollback_sql="DROP TABLE app.items")]


@pytest.mark.asyncio
async def test_create_migration_plan_returns_review_contract_without_database() -> None:
    response = await server.create_migration_plan("create-items", migration_input())
    payload = response_payload(response)

    assert isinstance(payload, dict)
    assert payload["name"] == "create-items"
    assert payload["applyable"] is True
    assert len(payload["review_hash"]) == 64
    assert "rollback:destructive_drop" in payload["warnings"]


@pytest.mark.asyncio
async def test_apply_requires_unrestricted_mode_before_service_creation() -> None:
    previous = server.current_access_mode
    server.current_access_mode = AccessMode.RESTRICTED
    try:
        with patch.object(server, "get_migration_service") as service_factory:
            response = await server.apply_migration_plan(
                "create-items",
                migration_input(),
                "a" * 64,
            )
        assert "requires --access-mode=unrestricted" in response_text(response)
        service_factory.assert_not_called()
    finally:
        server.current_access_mode = previous


@pytest.mark.asyncio
async def test_apply_rebuilds_plan_and_forwards_exact_review_hash() -> None:
    migration = MigrationPlanner().create_plan(
        name="create-items",
        steps=[MigrationStepDraft("CREATE TABLE app.items(id integer)", "DROP TABLE app.items")],
    )
    service = AsyncMock()
    service.apply.return_value = MigrationOperationResult(MigrationOperationStatus.APPLIED, None)
    previous = server.current_access_mode
    server.current_access_mode = AccessMode.UNRESTRICTED
    try:
        with (
            patch.object(server.migration_planner, "create_plan", return_value=migration) as create_plan,
            patch.object(server, "get_migration_service", return_value=service),
        ):
            response = await server.apply_migration_plan(
                migration.name,
                migration_input(),
                migration.review_hash,
                timeout_seconds=60,
                lock_timeout_seconds=4,
            )
    finally:
        server.current_access_mode = previous

    assert response_payload(response) == {
        "status": "applied",
        "committed": True,
        "database_changed": True,
        "idempotent": False,
        "migration": None,
    }
    create_plan.assert_called_once()
    service.apply.assert_awaited_once_with(
        migration,
        review_hash=migration.review_hash,
        timeout_seconds=60,
        lock_timeout_seconds=4,
    )


@pytest.mark.asyncio
async def test_migration_execution_error_reports_rollback_and_commit_certainty() -> None:
    migration = MigrationPlanner().create_plan(
        name="create-items",
        steps=[MigrationStepDraft("CREATE TABLE app.items(id integer)", "DROP TABLE app.items")],
    )
    service = AsyncMock()
    service.apply.side_effect = MigrationExecutionError(
        "failed and rolled back",
        phase="apply",
        failed_step=0,
        rollback_confirmed=True,
    )
    previous = server.current_access_mode
    server.current_access_mode = AccessMode.UNRESTRICTED
    try:
        with (
            patch.object(server.migration_planner, "create_plan", return_value=migration),
            patch.object(server, "get_migration_service", return_value=service),
        ):
            response = await server.apply_migration_plan(migration.name, migration_input(), migration.review_hash)
    finally:
        server.current_access_mode = previous

    assert response_payload(response) == {
        "status": "failed",
        "committed": False,
        "commit_state": "not_committed",
        "rolled_back": True,
        "phase": "apply",
        "failed_step": 0,
        "error": "failed and rolled back",
    }


@pytest.mark.asyncio
async def test_expected_domain_conflicts_are_stable_errors_without_traceback_surface() -> None:
    migration = MigrationPlanner().create_plan(
        name="create-items",
        steps=[MigrationStepDraft("CREATE TABLE app.items(id integer)", "DROP TABLE app.items")],
    )
    service = AsyncMock()
    service.apply.side_effect = MigrationConflictError("migration name conflicts with different reviewed content")
    previous = server.current_access_mode
    server.current_access_mode = AccessMode.UNRESTRICTED
    try:
        with (
            patch.object(server.migration_planner, "create_plan", return_value=migration),
            patch.object(server, "get_migration_service", return_value=service),
        ):
            response = await server.apply_migration_plan(migration.name, migration_input(), migration.review_hash)
    finally:
        server.current_access_mode = previous

    assert response_text(response) == "Error: migration name conflicts with different reviewed content"


@pytest.mark.asyncio
async def test_status_and_rollback_use_migration_service() -> None:
    service = AsyncMock()
    service.status.return_value = MigrationStatusSnapshot(())
    service.rollback.return_value = MigrationOperationResult(MigrationOperationStatus.ALREADY_ROLLED_BACK, None)

    with patch.object(server, "get_migration_service", return_value=service):
        status_response = await server.get_migration_status(limit=25)
    assert response_payload(status_response) == {"total_returned": 0, "migrations": []}
    service.status.assert_awaited_once_with(limit=25)

    previous = server.current_access_mode
    server.current_access_mode = AccessMode.UNRESTRICTED
    try:
        with patch.object(server, "get_migration_service", return_value=service):
            rollback_response = await server.rollback_migration("create-items", "b" * 64, timeout_seconds=20, lock_timeout_seconds=2)
    finally:
        server.current_access_mode = previous
    assert response_payload(rollback_response) == {
        "status": "already_rolled_back",
        "committed": False,
        "database_changed": False,
        "idempotent": True,
        "migration": None,
    }
    service.rollback.assert_awaited_once_with(
        name="create-items",
        review_hash="b" * 64,
        timeout_seconds=20,
        lock_timeout_seconds=2,
    )


@pytest.mark.asyncio
async def test_capabilities_advertise_reviewed_atomic_migrations() -> None:
    payload = response_payload(await server.get_server_capabilities())
    assert isinstance(payload, dict)
    assert payload["migrations"]["planning"] is True
    assert payload["migrations"]["review_hash_required"] is True
    assert payload["migrations"]["atomic_ledger"] is True
    assert payload["migrations"]["canonical_plan_ledger"] is True
    assert payload["migrations"]["rollback_policy_revalidated"] is True
    assert payload["migrations"]["ambiguous_commit_state_reported"] is True


def test_cli_exposes_existing_ledger_schema_without_changing_safe_defaults() -> None:
    parser = server.build_argument_parser()
    args = parser.parse_args(["postgresql://localhost/app", "--migration-schema", "platform"])
    assert args.migration_schema == "platform"
    assert args.access_mode == "restricted"
