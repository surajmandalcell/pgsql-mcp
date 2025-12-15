"""Migration tracking functionality for managing migration state in the database."""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from typing import cast

from typing_extensions import LiteralString
from typing_extensions import TypedDict

from ..sql import SqlDriver

logger = logging.getLogger(__name__)

MIGRATION_TABLE_NAME = "_postgres_mcp_migrations"


class MigrationStatusEntry(TypedDict):
    """Type for a single migration entry in status."""

    name: str
    applied_at: str
    batch: int


class MigrationStatus(TypedDict):
    """Type for migration status summary."""

    total_applied: int
    latest_batch: int
    migrations: list[MigrationStatusEntry]


@dataclass
class MigrationRecord:
    """Represents a migration record in the database."""

    id: int
    name: str
    applied_at: datetime
    checksum: str
    batch: int


class MigrationTracker:
    """Tracks migration state in the database."""

    def __init__(self, sql_driver: SqlDriver, schema: str = "public"):
        """Initialize the migration tracker.

        Args:
            sql_driver: SQL driver for database access
            schema: Schema where migration table is stored
        """
        self.sql_driver = sql_driver
        self.schema = schema
        self.table_name = f"{schema}.{MIGRATION_TABLE_NAME}"

    async def ensure_migration_table(self) -> None:
        """Create the migration tracking table if it doesn't exist."""
        create_table_sql = f"""
        CREATE TABLE IF NOT EXISTS {self.table_name} (
            id SERIAL PRIMARY KEY,
            name VARCHAR(255) NOT NULL UNIQUE,
            applied_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            checksum VARCHAR(64) NOT NULL,
            batch INTEGER NOT NULL,
            executed_sql TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_{MIGRATION_TABLE_NAME}_name
        ON {self.table_name} (name);

        CREATE INDEX IF NOT EXISTS idx_{MIGRATION_TABLE_NAME}_batch
        ON {self.table_name} (batch);
        """
        await self.sql_driver.execute_query(cast(LiteralString, create_table_sql))
        logger.info(f"Migration table ensured: {self.table_name}")

    async def get_applied_migrations(self) -> list[MigrationRecord]:
        """Get all applied migrations ordered by application time.

        Returns:
            List of applied migration records
        """
        query = f"""
        SELECT id, name, applied_at, checksum, batch
        FROM {self.table_name}
        ORDER BY id ASC
        """
        rows = await self.sql_driver.execute_query(cast(LiteralString, query))
        if not rows:
            return []

        return [
            MigrationRecord(
                id=row.cells["id"],
                name=row.cells["name"],
                applied_at=row.cells["applied_at"],
                checksum=row.cells["checksum"],
                batch=row.cells["batch"],
            )
            for row in rows
        ]

    async def get_latest_batch(self) -> int:
        """Get the latest batch number.

        Returns:
            The latest batch number, or 0 if no migrations exist
        """
        query = f"""
        SELECT COALESCE(MAX(batch), 0) as latest_batch
        FROM {self.table_name}
        """
        rows = await self.sql_driver.execute_query(cast(LiteralString, query))
        if rows:
            return rows[0].cells["latest_batch"]
        return 0

    async def record_migration(
        self,
        name: str,
        checksum: str,
        batch: int,
        executed_sql: Optional[str] = None,
    ) -> None:
        """Record a migration as applied.

        Args:
            name: Migration name
            checksum: SHA256 checksum of migration content
            batch: Batch number for this migration
            executed_sql: Optional SQL that was executed
        """
        # Use parameterized query for safety
        query = f"""
        INSERT INTO {self.table_name} (name, checksum, batch, executed_sql)
        VALUES (%s, %s, %s, %s)
        """
        await self.sql_driver.execute_query(
            cast(LiteralString, query),
            params=[name, checksum, batch, executed_sql],
        )
        logger.info(f"Recorded migration: {name} (batch {batch})")

    async def remove_migration(self, name: str) -> None:
        """Remove a migration record (for rollback).

        Args:
            name: Migration name to remove
        """
        query = f"""
        DELETE FROM {self.table_name}
        WHERE name = %s
        """
        await self.sql_driver.execute_query(cast(LiteralString, query), params=[name])
        logger.info(f"Removed migration record: {name}")

    async def get_migrations_in_batch(self, batch: int) -> list[MigrationRecord]:
        """Get all migrations in a specific batch.

        Args:
            batch: Batch number

        Returns:
            List of migrations in the batch, ordered by id DESC for rollback
        """
        query = f"""
        SELECT id, name, applied_at, checksum, batch
        FROM {self.table_name}
        WHERE batch = %s
        ORDER BY id DESC
        """
        rows = await self.sql_driver.execute_query(cast(LiteralString, query), params=[batch])
        if not rows:
            return []

        return [
            MigrationRecord(
                id=row.cells["id"],
                name=row.cells["name"],
                applied_at=row.cells["applied_at"],
                checksum=row.cells["checksum"],
                batch=row.cells["batch"],
            )
            for row in rows
        ]

    async def is_migration_applied(self, name: str) -> bool:
        """Check if a migration has been applied.

        Args:
            name: Migration name

        Returns:
            True if migration has been applied
        """
        query = f"""
        SELECT 1 FROM {self.table_name}
        WHERE name = %s
        """
        rows = await self.sql_driver.execute_query(cast(LiteralString, query), params=[name])
        return bool(rows)

    async def get_migration_status(self) -> MigrationStatus:
        """Get migration status summary.

        Returns:
            Dictionary with migration status information
        """
        applied = await self.get_applied_migrations()
        latest_batch = await self.get_latest_batch()

        return MigrationStatus(
            total_applied=len(applied),
            latest_batch=latest_batch,
            migrations=[
                MigrationStatusEntry(
                    name=m.name,
                    applied_at=m.applied_at.isoformat(),
                    batch=m.batch,
                )
                for m in applied
            ],
        )
