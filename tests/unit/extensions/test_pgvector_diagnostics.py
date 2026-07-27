"""Test-first contracts for read-only pgvector catalog diagnostics."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from postgres_mcp.pgvector_diagnostics import MAX_PGVECTOR_ITEMS
from postgres_mcp.pgvector_diagnostics import PgvectorCatalogError
from postgres_mcp.pgvector_diagnostics import PostgresPgvectorRepository
from postgres_mcp.pgvector_diagnostics import parse_dimensions
from postgres_mcp.sql import BoundedQueryResult


def result(rows: list[dict[str, object]], *, truncated: bool = False) -> BoundedQueryResult:
    return BoundedQueryResult(
        rows=rows,
        columns=[],
        row_count=len(rows),
        truncated=truncated,
        affected_rows=None,
        command="SELECT",
    )


def identity_row() -> dict[str, object]:
    return {
        "extension_oid": 9001,
        "installed_version": "0.8.0",
        "schema_name": "extensions",
    }


def column_row() -> dict[str, object]:
    return {
        "schema_name": "app",
        "relation_name": "items",
        "column_name": "embedding",
        "type_name": "vector",
        "formatted_type": "extensions.vector(1536)",
        "nullable": False,
    }


def index_row() -> dict[str, object]:
    return {
        "schema_name": "app",
        "relation_name": "items",
        "index_name": "items_embedding_hnsw_idx",
        "access_method": "hnsw",
        "is_unique": False,
        "is_valid": True,
        "is_ready": True,
        "predicate": None,
        "expression": None,
        "definition": "CREATE INDEX items_embedding_hnsw_idx ON app.items USING hnsw (embedding vector_cosine_ops)",
        "operator_classes": ["vector_cosine_ops"],
        "reloptions": ["m=16", "ef_construction=64"],
    }


@pytest.mark.parametrize(
    ("formatted_type", "expected"),
    [
        ("vector(1536)", 1536),
        ("extensions.vector(3)", 3),
        ("halfvec(4000)", 4000),
        ("sparsevec(1000)", 1000),
        ("bit(64000)", 64000),
        ("vector", None),
    ],
)
def test_parse_dimensions_uses_only_catalog_format_text(formatted_type: str, expected: int | None) -> None:
    assert parse_dimensions(formatted_type) == expected


def test_parse_dimensions_rejects_malformed_or_unsupported_type_text() -> None:
    with pytest.raises(PgvectorCatalogError, match="formatted type"):
        parse_dimensions("vector(not-a-number)")
    with pytest.raises(PgvectorCatalogError, match="supported pgvector type"):
        parse_dimensions("numeric(10,2)")


@pytest.mark.asyncio
async def test_repository_reads_identity_columns_and_indexes_with_fixed_bounds() -> None:
    driver = AsyncMock()
    driver.execute_bounded_query.side_effect = [
        result([identity_row()]),
        result([column_row()]),
        result([index_row()]),
    ]
    repository = PostgresPgvectorRepository(driver, timeout_seconds=4)

    snapshot = await repository.snapshot(max_columns=40, max_indexes=30)

    assert snapshot.identity.installed_version == "0.8.0"
    assert snapshot.columns[0].dimensions == 1536
    assert snapshot.indexes[0].operator_classes == ("vector_cosine_ops",)
    assert snapshot.indexes[0].options == {"ef_construction": "64", "m": "16"}
    assert snapshot.truncated is False

    calls = driver.execute_bounded_query.await_args_list
    assert calls[0].kwargs == {"max_rows": 1, "force_readonly": True, "timeout_seconds": 4}
    assert calls[1].kwargs == {
        "params": [9001],
        "max_rows": 40,
        "force_readonly": True,
        "timeout_seconds": 4,
    }
    assert calls[2].kwargs == {
        "params": [9001],
        "max_rows": 30,
        "force_readonly": True,
        "timeout_seconds": 4,
    }
    combined_sql = " ".join(" ".join(call.args[0].split()).lower() for call in calls)
    assert "pg_catalog.pg_extension" in combined_sql
    assert "pg_catalog.pg_attribute" in combined_sql
    assert "pg_catalog.pg_index" in combined_sql
    assert "vector_dims" not in combined_sql
    assert "pgvector" not in combined_sql


@pytest.mark.asyncio
async def test_repository_reports_missing_or_ambiguous_extension_identity() -> None:
    driver = AsyncMock()
    repository = PostgresPgvectorRepository(driver)

    driver.execute_bounded_query.return_value = result([])
    with pytest.raises(PgvectorCatalogError, match="not installed"):
        await repository.snapshot()

    driver.execute_bounded_query.return_value = result([identity_row(), identity_row()])
    with pytest.raises(PgvectorCatalogError, match="exactly one row"):
        await repository.snapshot()

    driver.execute_bounded_query.return_value = result([identity_row()], truncated=True)
    with pytest.raises(PgvectorCatalogError, match="exactly one row"):
        await repository.snapshot()


@pytest.mark.asyncio
async def test_repository_preserves_truncation_from_either_catalog() -> None:
    driver = AsyncMock()
    driver.execute_bounded_query.side_effect = [
        result([identity_row()]),
        result([column_row()], truncated=True),
        result([index_row()]),
    ]

    snapshot = await PostgresPgvectorRepository(driver).snapshot(max_columns=1, max_indexes=1)

    assert snapshot.truncated is True


@pytest.mark.asyncio
async def test_repository_rejects_malformed_catalog_rows() -> None:
    driver = AsyncMock()
    malformed = column_row()
    malformed["nullable"] = 1
    driver.execute_bounded_query.side_effect = [
        result([identity_row()]),
        result([malformed]),
        result([]),
    ]

    with pytest.raises(PgvectorCatalogError, match="nullable"):
        await PostgresPgvectorRepository(driver).snapshot()


@pytest.mark.asyncio
async def test_repository_rejects_duplicate_column_and_index_identities() -> None:
    driver = AsyncMock()
    driver.execute_bounded_query.side_effect = [
        result([identity_row()]),
        result([column_row(), column_row()]),
        result([]),
    ]
    with pytest.raises(PgvectorCatalogError, match="duplicate pgvector column"):
        await PostgresPgvectorRepository(driver).snapshot()

    driver.reset_mock()
    driver.execute_bounded_query.side_effect = [
        result([identity_row()]),
        result([]),
        result([index_row(), index_row()]),
    ]
    with pytest.raises(PgvectorCatalogError, match="duplicate pgvector index"):
        await PostgresPgvectorRepository(driver).snapshot()


def test_repository_limits_and_timeout_are_bounded() -> None:
    driver = AsyncMock()
    with pytest.raises(ValueError, match="positive"):
        PostgresPgvectorRepository(driver, timeout_seconds=0)

    repository = PostgresPgvectorRepository(driver)
    for max_columns, max_indexes in ((0, 1), (1, 0), (MAX_PGVECTOR_ITEMS, 1), (1, MAX_PGVECTOR_ITEMS)):
        with pytest.raises(PgvectorCatalogError, match="combined item limit"):
            repository._validate_limits(max_columns, max_indexes)  # pyright: ignore[reportPrivateUsage]


def test_snapshot_payload_reports_findings_without_claiming_runtime_recall() -> None:
    driver = AsyncMock()
    repository = PostgresPgvectorRepository(driver)
    snapshot = repository._snapshot_from_rows(  # pyright: ignore[reportPrivateUsage]
        identity_row(),
        [column_row()],
        [
            {
                **index_row(),
                "is_valid": False,
                "is_ready": False,
                "predicate": "tenant_id IS NOT NULL",
                "operator_classes": ["future_vector_ops"],
            }
        ],
        truncated=False,
    )

    payload = snapshot.to_payload()
    assert payload["columns"][0]["dimensions"] == 1536
    assert payload["indexes"][0]["operator_classes"] == ["future_vector_ops"]
    assert payload["findings"] == [
        {
            "code": "pgvector_index_not_valid",
            "severity": "error",
            "object": "app.items_embedding_hnsw_idx",
        },
        {
            "code": "pgvector_index_not_ready",
            "severity": "warning",
            "object": "app.items_embedding_hnsw_idx",
        },
        {
            "code": "pgvector_partial_index",
            "severity": "info",
            "object": "app.items_embedding_hnsw_idx",
        },
        {
            "code": "pgvector_unknown_operator_class",
            "severity": "info",
            "object": "app.items_embedding_hnsw_idx",
        },
    ]
    assert "recall" not in str(payload).lower()
