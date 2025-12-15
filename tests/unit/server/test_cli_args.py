"""Tests for CLI argument parsing and configuration."""

import os
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from postgres_mcp.server import DEFAULT_QUERY_TIMEOUT
from postgres_mcp.server import DEFAULT_SSE_HOST
from postgres_mcp.server import DEFAULT_SSE_PATH
from postgres_mcp.server import DEFAULT_SSE_PORT
from postgres_mcp.server import AccessMode


class TestCLIArgumentParsing:
    """Tests for command-line argument parsing."""

    @pytest.fixture
    def mock_environment(self):
        """Clear relevant environment variables."""
        env_vars = [
            "DATABASE_URI",
            "SSE_HOST",
            "SSE_PORT",
            "SSE_PATH",
            "CORS_ALLOW_ORIGINS",
            "QUERY_TIMEOUT",
        ]
        with patch.dict(os.environ, {}, clear=False):
            for var in env_vars:
                os.environ.pop(var, None)
            yield

    @pytest.mark.asyncio
    async def test_cli_database_url_positional(self, mock_environment):
        """Test database URL as positional argument."""
        from postgres_mcp.server import main

        test_url = "postgresql://user:pass@localhost:5432/testdb"

        with (
            patch("sys.argv", ["postgres-mcp", test_url]),
            patch("postgres_mcp.server.db_connection") as mock_conn,
            patch("postgres_mcp.server.mcp") as mock_mcp,
        ):
            mock_conn.pool_connect = AsyncMock()
            mock_mcp.run_stdio_async = AsyncMock()
            mock_mcp.add_tool = MagicMock()

            try:
                await main()
            except SystemExit:
                pass

            # Verify pool_connect was called with the correct URL
            mock_conn.pool_connect.assert_called_once_with(test_url)

    @pytest.mark.asyncio
    async def test_cli_database_url_from_env(self, mock_environment):
        """Test DATABASE_URI environment variable."""
        from postgres_mcp.server import main

        test_url = "postgresql://user:pass@localhost:5432/envdb"
        os.environ["DATABASE_URI"] = test_url

        with (
            patch("sys.argv", ["postgres-mcp"]),
            patch("postgres_mcp.server.db_connection") as mock_conn,
            patch("postgres_mcp.server.mcp") as mock_mcp,
        ):
            mock_conn.pool_connect = AsyncMock()
            mock_mcp.run_stdio_async = AsyncMock()
            mock_mcp.add_tool = MagicMock()

            try:
                await main()
            except SystemExit:
                pass

            mock_conn.pool_connect.assert_called_once_with(test_url)

    @pytest.mark.asyncio
    async def test_cli_access_mode_unrestricted(self, mock_environment):
        """Test --access-mode=unrestricted."""
        import postgres_mcp.server

        test_url = "postgresql://user:pass@localhost:5432/testdb"

        with (
            patch("sys.argv", ["postgres-mcp", test_url, "--access-mode=unrestricted"]),
            patch("postgres_mcp.server.db_connection") as mock_conn,
            patch("postgres_mcp.server.mcp") as mock_mcp,
        ):
            mock_conn.pool_connect = AsyncMock()
            mock_mcp.run_stdio_async = AsyncMock()
            mock_mcp.add_tool = MagicMock()

            # Reset to restricted to verify change
            postgres_mcp.server.current_access_mode = AccessMode.RESTRICTED

            try:
                await postgres_mcp.server.main()
            except SystemExit:
                pass

            assert postgres_mcp.server.current_access_mode == AccessMode.UNRESTRICTED

    @pytest.mark.asyncio
    async def test_cli_access_mode_restricted(self, mock_environment):
        """Test --access-mode=restricted."""
        import postgres_mcp.server

        test_url = "postgresql://user:pass@localhost:5432/testdb"

        with (
            patch("sys.argv", ["postgres-mcp", test_url, "--access-mode=restricted"]),
            patch("postgres_mcp.server.db_connection") as mock_conn,
            patch("postgres_mcp.server.mcp") as mock_mcp,
        ):
            mock_conn.pool_connect = AsyncMock()
            mock_mcp.run_stdio_async = AsyncMock()
            mock_mcp.add_tool = MagicMock()

            # Reset to unrestricted to verify change
            postgres_mcp.server.current_access_mode = AccessMode.UNRESTRICTED

            try:
                await postgres_mcp.server.main()
            except SystemExit:
                pass

            assert postgres_mcp.server.current_access_mode == AccessMode.RESTRICTED

    @pytest.mark.asyncio
    async def test_cli_transport_stdio(self, mock_environment):
        """Test --transport=stdio (default)."""
        from postgres_mcp.server import main

        test_url = "postgresql://user:pass@localhost:5432/testdb"

        with (
            patch("sys.argv", ["postgres-mcp", test_url, "--transport=stdio"]),
            patch("postgres_mcp.server.db_connection") as mock_conn,
            patch("postgres_mcp.server.mcp") as mock_mcp,
        ):
            mock_conn.pool_connect = AsyncMock()
            mock_mcp.run_stdio_async = AsyncMock()
            mock_mcp.run_sse_async = AsyncMock()
            mock_mcp.add_tool = MagicMock()

            try:
                await main()
            except SystemExit:
                pass

            mock_mcp.run_stdio_async.assert_called_once()
            mock_mcp.run_sse_async.assert_not_called()

    @pytest.mark.asyncio
    async def test_cli_transport_sse(self, mock_environment):
        """Test --transport=sse."""
        from postgres_mcp.server import main

        test_url = "postgresql://user:pass@localhost:5432/testdb"

        with (
            patch("sys.argv", ["postgres-mcp", test_url, "--transport=sse"]),
            patch("postgres_mcp.server.db_connection") as mock_conn,
            patch("postgres_mcp.server.mcp") as mock_mcp,
        ):
            mock_conn.pool_connect = AsyncMock()
            mock_mcp.run_stdio_async = AsyncMock()
            mock_mcp.run_sse_async = AsyncMock()
            mock_mcp.add_tool = MagicMock()
            mock_mcp.settings = MagicMock()

            try:
                await main()
            except SystemExit:
                pass

            mock_mcp.run_sse_async.assert_called_once()
            mock_mcp.run_stdio_async.assert_not_called()

    @pytest.mark.asyncio
    async def test_cli_sse_host(self, mock_environment):
        """Test --sse-host argument."""
        from postgres_mcp.server import main

        test_url = "postgresql://user:pass@localhost:5432/testdb"
        custom_host = "0.0.0.0"

        with (
            patch("sys.argv", ["postgres-mcp", test_url, "--transport=sse", f"--sse-host={custom_host}"]),
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

            assert mock_mcp.settings.host == custom_host

    @pytest.mark.asyncio
    async def test_cli_sse_port(self, mock_environment):
        """Test --sse-port argument."""
        from postgres_mcp.server import main

        test_url = "postgresql://user:pass@localhost:5432/testdb"
        custom_port = 9000

        with (
            patch("sys.argv", ["postgres-mcp", test_url, "--transport=sse", f"--sse-port={custom_port}"]),
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

            assert mock_mcp.settings.port == custom_port

    @pytest.mark.asyncio
    async def test_cli_sse_path(self, mock_environment):
        """Test --sse-path argument."""
        from postgres_mcp.server import main

        test_url = "postgresql://user:pass@localhost:5432/testdb"
        custom_path = "/custom/sse"

        with (
            patch("sys.argv", ["postgres-mcp", test_url, "--transport=sse", f"--sse-path={custom_path}"]),
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

            assert mock_mcp.settings.sse_path == custom_path

    @pytest.mark.asyncio
    async def test_cli_query_timeout(self, mock_environment):
        """Test --query-timeout argument."""
        import postgres_mcp.server

        test_url = "postgresql://user:pass@localhost:5432/testdb"
        custom_timeout = 60

        with (
            patch("sys.argv", ["postgres-mcp", test_url, f"--query-timeout={custom_timeout}"]),
            patch("postgres_mcp.server.db_connection") as mock_conn,
            patch("postgres_mcp.server.mcp") as mock_mcp,
        ):
            mock_conn.pool_connect = AsyncMock()
            mock_mcp.run_stdio_async = AsyncMock()
            mock_mcp.add_tool = MagicMock()

            try:
                await postgres_mcp.server.main()
            except SystemExit:
                pass

            assert postgres_mcp.server.current_query_timeout == custom_timeout

    @pytest.mark.asyncio
    async def test_cli_env_sse_host(self, mock_environment):
        """Test SSE_HOST environment variable."""
        from postgres_mcp.server import main

        test_url = "postgresql://user:pass@localhost:5432/testdb"
        custom_host = "192.168.1.1"
        os.environ["SSE_HOST"] = custom_host

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

            assert mock_mcp.settings.host == custom_host

    @pytest.mark.asyncio
    async def test_cli_env_sse_port(self, mock_environment):
        """Test SSE_PORT environment variable."""
        from postgres_mcp.server import main

        test_url = "postgresql://user:pass@localhost:5432/testdb"
        custom_port = "9500"
        os.environ["SSE_PORT"] = custom_port

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

            assert mock_mcp.settings.port == int(custom_port)

    @pytest.mark.asyncio
    async def test_cli_env_sse_path(self, mock_environment):
        """Test SSE_PATH environment variable."""
        from postgres_mcp.server import main

        test_url = "postgresql://user:pass@localhost:5432/testdb"
        custom_path = "/api/sse"
        os.environ["SSE_PATH"] = custom_path

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

            assert mock_mcp.settings.sse_path == custom_path

    @pytest.mark.asyncio
    async def test_cli_env_query_timeout(self, mock_environment):
        """Test QUERY_TIMEOUT environment variable."""
        import postgres_mcp.server

        test_url = "postgresql://user:pass@localhost:5432/testdb"
        custom_timeout = "120"
        os.environ["QUERY_TIMEOUT"] = custom_timeout

        with (
            patch("sys.argv", ["postgres-mcp", test_url]),
            patch("postgres_mcp.server.db_connection") as mock_conn,
            patch("postgres_mcp.server.mcp") as mock_mcp,
        ):
            mock_conn.pool_connect = AsyncMock()
            mock_mcp.run_stdio_async = AsyncMock()
            mock_mcp.add_tool = MagicMock()

            try:
                await postgres_mcp.server.main()
            except SystemExit:
                pass

            assert postgres_mcp.server.current_query_timeout == int(custom_timeout)

    @pytest.mark.asyncio
    async def test_cli_arg_precedence_over_env(self, mock_environment):
        """Test that CLI arguments override environment variables."""
        from postgres_mcp.server import main

        test_url = "postgresql://user:pass@localhost:5432/testdb"
        env_host = "env-host"
        cli_host = "cli-host"
        os.environ["SSE_HOST"] = env_host

        with (
            patch("sys.argv", ["postgres-mcp", test_url, "--transport=sse", f"--sse-host={cli_host}"]),
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

            # CLI should win over env
            assert mock_mcp.settings.host == cli_host

    @pytest.mark.asyncio
    async def test_cli_invalid_sse_port(self, mock_environment):
        """Test invalid SSE_PORT value falls back to default."""
        import postgres_mcp.server

        test_url = "postgresql://user:pass@localhost:5432/testdb"
        os.environ["SSE_PORT"] = "not_a_number"

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
                await postgres_mcp.server.main()
            except SystemExit:
                pass

            # Should use default port when invalid
            assert mock_mcp.settings.port == DEFAULT_SSE_PORT

    @pytest.mark.asyncio
    async def test_cli_invalid_query_timeout(self, mock_environment):
        """Test invalid QUERY_TIMEOUT value falls back to default."""
        import postgres_mcp.server

        test_url = "postgresql://user:pass@localhost:5432/testdb"
        os.environ["QUERY_TIMEOUT"] = "invalid"

        with (
            patch("sys.argv", ["postgres-mcp", test_url]),
            patch("postgres_mcp.server.db_connection") as mock_conn,
            patch("postgres_mcp.server.mcp") as mock_mcp,
        ):
            mock_conn.pool_connect = AsyncMock()
            mock_mcp.run_stdio_async = AsyncMock()
            mock_mcp.add_tool = MagicMock()

            try:
                await postgres_mcp.server.main()
            except SystemExit:
                pass

            # Should use default timeout when invalid
            assert postgres_mcp.server.current_query_timeout == DEFAULT_QUERY_TIMEOUT

    @pytest.mark.asyncio
    async def test_cli_missing_database_url(self, mock_environment):
        """Test error when no database URL provided."""
        from postgres_mcp.server import main

        with (
            patch("sys.argv", ["postgres-mcp"]),
            patch("postgres_mcp.server.db_connection") as mock_conn,
            patch("postgres_mcp.server.mcp") as mock_mcp,
        ):
            mock_conn.pool_connect = AsyncMock()
            mock_mcp.run_stdio_async = AsyncMock()
            mock_mcp.add_tool = MagicMock()

            with pytest.raises(ValueError, match="No database URL provided"):
                await main()


class TestCLIDefaults:
    """Tests for CLI default values."""

    def test_default_sse_host(self):
        """Test default SSE host value."""
        assert DEFAULT_SSE_HOST == "localhost"

    def test_default_sse_port(self):
        """Test default SSE port value."""
        assert DEFAULT_SSE_PORT == 8000

    def test_default_sse_path(self):
        """Test default SSE path value."""
        assert DEFAULT_SSE_PATH == "/sse"

    def test_default_query_timeout(self):
        """Test default query timeout value."""
        assert DEFAULT_QUERY_TIMEOUT == 30


class TestAccessModeEnum:
    """Tests for AccessMode enum."""

    def test_access_mode_values(self):
        """Test AccessMode enum values."""
        assert AccessMode.UNRESTRICTED.value == "unrestricted"
        assert AccessMode.RESTRICTED.value == "restricted"

    def test_access_mode_from_string(self):
        """Test creating AccessMode from string."""
        assert AccessMode("unrestricted") == AccessMode.UNRESTRICTED
        assert AccessMode("restricted") == AccessMode.RESTRICTED

    def test_access_mode_invalid_value(self):
        """Test invalid AccessMode value raises error."""
        with pytest.raises(ValueError):
            AccessMode("invalid")
