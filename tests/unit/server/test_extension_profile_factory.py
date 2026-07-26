"""Construction contract for the extension profile repository."""

from __future__ import annotations

from unittest.mock import MagicMock
from unittest.mock import patch

import postgres_mcp.server as server
from postgres_mcp.extension_profiles import PostgresExtensionProfileRepository


def test_extension_profile_repository_uses_base_driver_and_configured_timeout() -> None:
    driver = MagicMock()
    previous_timeout = server.current_query_timeout
    server.current_query_timeout = 0.25
    try:
        with patch.object(server, "get_base_sql_driver", return_value=driver):
            repository = server.get_extension_profile_repository()
    finally:
        server.current_query_timeout = previous_timeout

    assert isinstance(repository, PostgresExtensionProfileRepository)
    assert repository.sql_driver is driver
    assert repository.timeout_seconds == 1.0
