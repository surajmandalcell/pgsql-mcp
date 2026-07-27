from pathlib import Path


path = Path("src/postgres_mcp/sql/sql_driver.py")
source = path.read_text()

replacements = (
    ("        self._initialization_lock = asyncio.Lock()\n", "        self._lifecycle_lock = asyncio.Lock()\n"),
    ("        async with self._initialization_lock:\n", "        async with self._lifecycle_lock:\n"),
)
for old, new in replacements:
    if source.count(old) != 1:
        raise RuntimeError(f"expected one pool lifecycle marker: {old.strip()!r}")
    source = source.replace(old, new, 1)

pool_connect_start = source.index("    async def pool_connect(")
close_start = source.index("    async def close(", pool_connect_start)
pool_connect = source[pool_connect_start:close_start]
if pool_connect.count("await self.close()") != 2:
    raise RuntimeError("expected two pool-connect cleanup calls")
pool_connect = pool_connect.replace("await self.close()", "await self._close_unlocked()")
source = source[:pool_connect_start] + pool_connect + source[close_start:]

old_close = '''    async def close(self) -> None:
        """Close the pool and clear reusable state."""
        pool = self.pool
        self.pool = None
        self._is_valid = False
        if pool is not None:
            try:
                await pool.close()
            except Exception as exc:
                logger.warning("Error closing connection pool: %s", exc)
'''
new_close = '''    async def _close_unlocked(self) -> None:
        """Close the current pool while the caller owns the lifecycle lock."""
        pool = self.pool
        self.pool = None
        self._is_valid = False
        if pool is not None:
            try:
                await pool.close()
            except Exception as exc:
                logger.warning("Error closing connection pool: %s", exc)

    async def close(self) -> None:
        """Serialize pool shutdown against initialization and verification."""
        async with self._lifecycle_lock:
            await self._close_unlocked()
'''
if source.count(old_close) != 1:
    raise RuntimeError("expected one DbConnPool.close implementation")
path.write_text(source.replace(old_close, new_close, 1))
