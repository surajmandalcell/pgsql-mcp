"""MCP boundary contracts for deployment provider capability profiles."""

from __future__ import annotations

import json
import subprocess
import sys
from unittest.mock import AsyncMock
from unittest.mock import patch

import pytest
from mcp.types import TextContent

import postgres_mcp.server as server
from postgres_mcp.provider_profiles import DeploymentProvider
from postgres_mcp.provider_profiles import DetectionConfidence
from postgres_mcp.provider_profiles import ProviderConstraints
from postgres_mcp.provider_profiles import ProviderEvidence
from postgres_mcp.provider_profiles import ProviderProfileError
from postgres_mcp.provider_profiles import ProviderProfileSnapshot
from postgres_mcp.provider_profiles import RuntimeCapabilities


def response_text(response: server.ResponseType) -> str:
    content = response[0]
    assert isinstance(content, TextContent)
    return content.text


def response_payload(response: server.ResponseType) -> object:
    return json.loads(response_text(response))


def snapshot() -> ProviderProfileSnapshot:
    return ProviderProfileSnapshot(
        provider=DeploymentProvider.AWS_RDS,
        confidence=DetectionConfidence.HIGH,
        explicit_hint=None,
        evidence=(ProviderEvidence("setting_prefix", "rds.*", DeploymentProvider.AWS_RDS),),
        constraints=ProviderConstraints(
            host_os_access="unavailable",
            true_postgres_superuser="unavailable",
            extension_installation="rds_supported_and_allowlisted",
            failover_control="provider_managed",
            logical_replication="parameter_and_role_dependent",
            notes=("managed",),
        ),
        runtime=RuntimeCapabilities(
            server_version_num=180001,
            in_recovery=False,
            wal_level="logical",
            max_wal_senders=10,
            max_replication_slots=10,
        ),
        warnings=(),
    )


@pytest.mark.asyncio
async def test_provider_profile_tool_uses_bounded_readonly_repository() -> None:
    repository = AsyncMock()
    repository.snapshot.return_value = snapshot()

    with patch.object(server, "get_provider_profile_repository", return_value=repository):
        response = await server.get_deployment_profile(provider_hint="auto")

    payload = response_payload(response)
    assert isinstance(payload, dict)
    assert payload["provider"] == "aws_rds"
    assert payload["runtime"]["logical_replication_configured"] is True
    repository.snapshot.assert_awaited_once_with(provider_hint="auto")


@pytest.mark.asyncio
async def test_provider_profile_tool_reports_domain_and_unexpected_errors() -> None:
    repository = AsyncMock()
    repository.snapshot.side_effect = ProviderProfileError("invalid provider hint")
    with patch.object(server, "get_provider_profile_repository", return_value=repository):
        response = await server.get_deployment_profile(provider_hint="bad")
    assert response_text(response) == "Error: invalid provider hint"

    repository.snapshot.side_effect = RuntimeError("catalog unavailable")
    with patch.object(server, "get_provider_profile_repository", return_value=repository):
        response = await server.get_deployment_profile()
    assert response_text(response) == "Error: catalog unavailable"


@pytest.mark.asyncio
async def test_capabilities_advertise_conservative_provider_profiles() -> None:
    payload = response_payload(await server.get_server_capabilities())
    assert isinstance(payload, dict)
    assert payload["deployment_profiles"] == {
        "automatic_detection": "strong_markers_only",
        "unknown_without_marker": True,
        "explicit_hint_supported": True,
        "secrets_read": False,
        "supported_profiles": [
            "upstream",
            "generic_managed",
            "aws_rds",
            "aws_aurora",
            "google_cloud_sql",
            "google_alloydb",
            "azure_flexible_server",
            "neon",
            "supabase_hosted",
        ],
    }


def test_lite_and_ha_profiles_do_not_import_provider_domain() -> None:
    for module in ("postgres_mcp.lite_server", "postgres_mcp.ha_server"):
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                f"import sys; import {module}; assert 'postgres_mcp.provider_profiles' not in sys.modules",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, completed.stderr
