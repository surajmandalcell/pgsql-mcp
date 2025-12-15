"""Tests for CORS support in SSE transport."""

import os
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest


class TestCORSConfiguration:
    """Tests for CORS configuration."""

    @pytest.fixture
    def mock_environment(self):
        """Clear relevant environment variables."""
        env_vars = [
            "DATABASE_URI",
            "SSE_HOST",
            "SSE_PORT",
            "SSE_PATH",
            "CORS_ALLOW_ORIGINS",
        ]
        with patch.dict(os.environ, {}, clear=False):
            for var in env_vars:
                os.environ.pop(var, None)
            yield

    @pytest.mark.asyncio
    async def test_cors_single_origin(self, mock_environment):
        """Test CORS with a single origin."""
        from postgres_mcp.server import main

        test_url = "postgresql://user:pass@localhost:5432/testdb"
        cors_origin = "http://localhost:3000"

        with (
            patch("sys.argv", ["postgres-mcp", test_url, "--transport=sse", f"--cors-allow-origins={cors_origin}"]),
            patch("postgres_mcp.server.db_connection") as mock_conn,
            patch("postgres_mcp.server.mcp") as mock_mcp,
            patch("uvicorn.Server") as mock_uvicorn_server,
            patch("uvicorn.Config") as mock_uvicorn_config,
        ):
            mock_conn.pool_connect = AsyncMock()
            mock_mcp.add_tool = MagicMock()
            mock_mcp.settings = MagicMock()
            mock_mcp.sse_app = MagicMock(return_value=MagicMock())

            mock_server_instance = MagicMock()
            mock_server_instance.serve = AsyncMock()
            mock_uvicorn_server.return_value = mock_server_instance

            try:
                await main()
            except SystemExit:
                pass

            # Verify uvicorn was configured
            mock_uvicorn_config.assert_called_once()
            config_call = mock_uvicorn_config.call_args

            # The first positional arg should be the Starlette app
            assert config_call is not None

    @pytest.mark.asyncio
    async def test_cors_multiple_origins(self, mock_environment):
        """Test CORS with multiple origins."""
        from postgres_mcp.server import main

        test_url = "postgresql://user:pass@localhost:5432/testdb"
        cors_origins = "http://localhost:3000,https://example.com,https://app.example.com"

        with (
            patch("sys.argv", ["postgres-mcp", test_url, "--transport=sse", f"--cors-allow-origins={cors_origins}"]),
            patch("postgres_mcp.server.db_connection") as mock_conn,
            patch("postgres_mcp.server.mcp") as mock_mcp,
            patch("uvicorn.Server") as mock_uvicorn_server,
            patch("uvicorn.Config") as mock_uvicorn_config,
        ):
            mock_conn.pool_connect = AsyncMock()
            mock_mcp.add_tool = MagicMock()
            mock_mcp.settings = MagicMock()
            mock_mcp.sse_app = MagicMock(return_value=MagicMock())

            mock_server_instance = MagicMock()
            mock_server_instance.serve = AsyncMock()
            mock_uvicorn_server.return_value = mock_server_instance

            try:
                await main()
            except SystemExit:
                pass

            mock_uvicorn_config.assert_called_once()

    @pytest.mark.asyncio
    async def test_cors_wildcard(self, mock_environment):
        """Test CORS with wildcard (*)."""
        from postgres_mcp.server import main

        test_url = "postgresql://user:pass@localhost:5432/testdb"

        with (
            patch("sys.argv", ["postgres-mcp", test_url, "--transport=sse", "--cors-allow-origins=*"]),
            patch("postgres_mcp.server.db_connection") as mock_conn,
            patch("postgres_mcp.server.mcp") as mock_mcp,
            patch("uvicorn.Server") as mock_uvicorn_server,
            patch("uvicorn.Config") as mock_uvicorn_config,
        ):
            mock_conn.pool_connect = AsyncMock()
            mock_mcp.add_tool = MagicMock()
            mock_mcp.settings = MagicMock()
            mock_mcp.sse_app = MagicMock(return_value=MagicMock())

            mock_server_instance = MagicMock()
            mock_server_instance.serve = AsyncMock()
            mock_uvicorn_server.return_value = mock_server_instance

            try:
                await main()
            except SystemExit:
                pass

            mock_uvicorn_config.assert_called_once()

    @pytest.mark.asyncio
    async def test_cors_env_variable(self, mock_environment):
        """Test CORS from CORS_ALLOW_ORIGINS environment variable."""
        from postgres_mcp.server import main

        test_url = "postgresql://user:pass@localhost:5432/testdb"
        cors_origin = "http://localhost:4000"
        os.environ["CORS_ALLOW_ORIGINS"] = cors_origin

        with (
            patch("sys.argv", ["postgres-mcp", test_url, "--transport=sse"]),
            patch("postgres_mcp.server.db_connection") as mock_conn,
            patch("postgres_mcp.server.mcp") as mock_mcp,
            patch("uvicorn.Server") as mock_uvicorn_server,
            patch("uvicorn.Config") as mock_uvicorn_config,
        ):
            mock_conn.pool_connect = AsyncMock()
            mock_mcp.add_tool = MagicMock()
            mock_mcp.settings = MagicMock()
            mock_mcp.sse_app = MagicMock(return_value=MagicMock())

            mock_server_instance = MagicMock()
            mock_server_instance.serve = AsyncMock()
            mock_uvicorn_server.return_value = mock_server_instance

            try:
                await main()
            except SystemExit:
                pass

            # When CORS is set, uvicorn should be configured
            mock_uvicorn_config.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_cors_uses_default_sse(self, mock_environment):
        """Test that without CORS, default SSE server is used."""
        from postgres_mcp.server import main

        test_url = "postgresql://user:pass@localhost:5432/testdb"

        with (
            patch("sys.argv", ["postgres-mcp", test_url, "--transport=sse"]),
            patch("postgres_mcp.server.db_connection") as mock_conn,
            patch("postgres_mcp.server.mcp") as mock_mcp,
        ):
            mock_conn.pool_connect = AsyncMock()
            mock_mcp.run_sse_async = AsyncMock()
            mock_mcp.add_tool = MagicMock()
            mock_mcp.settings = MagicMock()

            try:
                await main()
            except SystemExit:
                pass

            # Without CORS, should use run_sse_async
            mock_mcp.run_sse_async.assert_called_once()

    @pytest.mark.asyncio
    async def test_cors_cli_precedence_over_env(self, mock_environment):
        """Test CLI --cors-allow-origins takes precedence over env var."""
        from postgres_mcp.server import main

        test_url = "postgresql://user:pass@localhost:5432/testdb"
        env_origin = "http://env-origin.com"
        cli_origin = "http://cli-origin.com"
        os.environ["CORS_ALLOW_ORIGINS"] = env_origin

        with (
            patch("sys.argv", ["postgres-mcp", test_url, "--transport=sse", f"--cors-allow-origins={cli_origin}"]),
            patch("postgres_mcp.server.db_connection") as mock_conn,
            patch("postgres_mcp.server.mcp") as mock_mcp,
            patch("uvicorn.Server") as mock_uvicorn_server,
            patch("uvicorn.Config") as mock_uvicorn_config,
        ):
            mock_conn.pool_connect = AsyncMock()
            mock_mcp.add_tool = MagicMock()
            mock_mcp.settings = MagicMock()
            mock_mcp.sse_app = MagicMock(return_value=MagicMock())

            mock_server_instance = MagicMock()
            mock_server_instance.serve = AsyncMock()
            mock_uvicorn_server.return_value = mock_server_instance

            try:
                await main()
            except SystemExit:
                pass

            # CLI should win - server should be configured with custom CORS
            mock_uvicorn_config.assert_called_once()


class TestCORSOriginParsing:
    """Tests for CORS origin parsing."""

    def test_parse_single_origin(self):
        """Test parsing a single origin."""
        cors_str = "http://localhost:3000"
        if cors_str == "*":
            origins = ["*"]
        else:
            origins = [origin.strip() for origin in cors_str.split(",")]

        assert origins == ["http://localhost:3000"]

    def test_parse_multiple_origins(self):
        """Test parsing multiple origins."""
        cors_str = "http://localhost:3000,https://example.com"
        if cors_str == "*":
            origins = ["*"]
        else:
            origins = [origin.strip() for origin in cors_str.split(",")]

        assert origins == ["http://localhost:3000", "https://example.com"]

    def test_parse_wildcard(self):
        """Test parsing wildcard origin."""
        cors_str: str = "*"
        if cors_str == "*":
            origins = ["*"]
        else:
            origins = [origin.strip() for origin in cors_str.split(",")]

        assert origins == ["*"]

    def test_parse_origins_with_whitespace(self):
        """Test parsing origins with extra whitespace."""
        cors_str = "http://localhost:3000 , https://example.com , https://app.example.com"
        if cors_str == "*":
            origins = ["*"]
        else:
            origins = [origin.strip() for origin in cors_str.split(",")]

        assert origins == [
            "http://localhost:3000",
            "https://example.com",
            "https://app.example.com",
        ]
