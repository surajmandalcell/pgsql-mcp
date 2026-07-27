"""Real-PostgreSQL contracts for pgvector catalog diagnostics."""

from __future__ import annotations

import pytest

from postgres_mcp.pgvector_diagnostics import PostgresPgvectorRepository
from postgres_mcp.sql import SqlDriver


@pytest.mark.asyncio
async def test_pgvector_columns_and_hnsw_index_use_only_catalog_metadata(
    test_postgres_connection_string: tuple[str, str],
) -> None:
    connection_string, _version = test_postgres_connection_string
    driver = SqlDriver(engine_url=connection_string)

    try:
        async with driver.connection() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute("DROP SCHEMA IF EXISTS pgvector_contract CASCADE")
                await cursor.execute("CREATE SCHEMA pgvector_contract")
                await cursor.execute(
                    """
                    CREATE TABLE pgvector_contract.items (
                        id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                        embedding vector(3) NOT NULL,
                        compact halfvec(3)
                    )
                    """
                )
                await cursor.execute(
                    """
                    CREATE INDEX items_embedding_hnsw_idx
                    ON pgvector_contract.items
                    USING hnsw (embedding vector_cosine_ops)
                    WITH (m = 8, ef_construction = 32)
                    """
                )
            await connection.commit()

        repository = PostgresPgvectorRepository(driver, timeout_seconds=10)
        first = await repository.snapshot(max_columns=100, max_indexes=100)
        second = await repository.snapshot(max_columns=100, max_indexes=100)

        assert first == second
        assert first.identity.installed_version == "0.8.2"
        assert first.identity.oid > 0
        assert first.truncated is False

        columns = {(column.schema, column.relation, column.column): column for column in first.columns if column.schema == "pgvector_contract"}
        assert columns[("pgvector_contract", "items", "embedding")].dimensions == 3
        assert columns[("pgvector_contract", "items", "embedding")].nullable is False
        assert columns[("pgvector_contract", "items", "compact")].type_name == "halfvec"
        assert columns[("pgvector_contract", "items", "compact")].dimensions == 3

        index = next(item for item in first.indexes if item.name == "items_embedding_hnsw_idx")
        assert index.schema == "pgvector_contract"
        assert index.relation == "items"
        assert index.access_method == "hnsw"
        assert index.operator_classes == ("vector_cosine_ops",)
        assert index.options == {"ef_construction": "32", "m": "8"}
        assert index.valid is True
        assert index.ready is True
        assert first.findings == ()

        payload_text = repr(first.to_payload()).lower()
        assert "password" not in payload_text
        assert "conninfo" not in payload_text
        assert connection_string.lower() not in payload_text
    finally:
        try:
            async with driver.connection() as connection:
                async with connection.cursor() as cursor:
                    await cursor.execute("DROP SCHEMA IF EXISTS pgvector_contract CASCADE")
                await connection.commit()
        finally:
            pool = driver.connect()
            if hasattr(pool, "close"):
                await pool.close()
