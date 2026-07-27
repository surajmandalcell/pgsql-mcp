from pathlib import Path


path = Path("src/postgres_mcp/sql/sql_driver.py")
source = path.read_text()

old_state = """        self.pool: AsyncConnectionPool | None = None
        self._is_valid = False
        self._last_error: str | None = None
"""
new_state = """        self.pool: AsyncConnectionPool | None = None
        self._is_valid = False
        self._last_error: str | None = None
        self._initialization_lock = asyncio.Lock()
"""
if source.count(old_state) != 1:
    raise RuntimeError("expected one DbConnPool lifecycle state block")
source = source.replace(old_state, new_state, 1)

old_method = '''    async def pool_connect(self, connection_url: str | None = None) -> AsyncConnectionPool:
        """Initialize and verify the connection pool."""
        if self.pool and self._is_valid:
            return self.pool

        url = connection_url or self.connection_url
        self.connection_url = url
        if not url:
            self._is_valid = False
            self._last_error = "Database connection URL not provided"
            raise ValueError(self._last_error)

        await self.close()
        try:
            self.pool = AsyncConnectionPool(
                conninfo=url,
                min_size=self.min_size,
                max_size=self.max_size,
                open=False,
            )
            await self.pool.open()
            async with self.pool.connection() as conn:
                async with conn.cursor() as cursor:
                    await cursor.execute("SELECT 1")
            self._is_valid = True
            self._last_error = None
            return self.pool
        except Exception as exc:
            self._is_valid = False
            self._last_error = str(exc)
            await self.close()
            raise ValueError(f"Connection attempt failed: {obfuscate_password(str(exc))}") from exc
'''
new_method = '''    async def pool_connect(self, connection_url: str | None = None) -> AsyncConnectionPool:
        """Initialize and verify the connection pool exactly once under concurrency."""
        if self.pool and self._is_valid:
            return self.pool

        async with self._initialization_lock:
            if self.pool and self._is_valid:
                return self.pool

            url = connection_url or self.connection_url
            self.connection_url = url
            if not url:
                self._is_valid = False
                self._last_error = "Database connection URL not provided"
                raise ValueError(self._last_error)

            await self.close()
            try:
                candidate = AsyncConnectionPool(
                    conninfo=url,
                    min_size=self.min_size,
                    max_size=self.max_size,
                    open=False,
                )
                self.pool = candidate
                await candidate.open()
                async with candidate.connection() as conn:
                    async with conn.cursor() as cursor:
                        await cursor.execute("SELECT 1")
                self._is_valid = True
                self._last_error = None
                return candidate
            except Exception as exc:
                self._is_valid = False
                self._last_error = str(exc)
                await self.close()
                raise ValueError(f"Connection attempt failed: {obfuscate_password(str(exc))}") from exc
'''
if source.count(old_method) != 1:
    raise RuntimeError("expected one DbConnPool.pool_connect implementation")
path.write_text(source.replace(old_method, new_method, 1))
