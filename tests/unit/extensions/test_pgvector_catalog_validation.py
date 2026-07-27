"""Defensive catalog-validation contracts for pgvector diagnostics."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from postgres_mcp.pgvector_diagnostics import PgvectorCatalogError
from postgres_mcp.pgvector_diagnostics import PostgresPgvectorRepository
from postgres_mcp.pgvector_diagnostics import parse_dimensions


def identity_row() -> dict[str, object]:
    return {
        "extension_oid": 9001,
        "installed_version": "0.8.2",
        "schema_name": "extensions",
    }


def column_row() -> dict[str, object]:
    return {
        "schema_name": "app",
        "relation_name": "items",
        "column_name": "embedding",
        "type_name": "vector",
        "formatted_type": "vector(3)",
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
        "reloptions": ["m=16"],
    }


def snapshot(
    identity: dict[str, object] | None = None,
    columns: list[dict[str, object]] | None = None,
    indexes: list[dict[str, object]] | None = None,
) -> object:
    repository = PostgresPgvectorRepository(AsyncMock())
    return repository._snapshot_from_rows(  # pyright: ignore[reportPrivateUsage]
        identity or identity_row(),
        columns if columns is not None else [column_row()],
        indexes if indexes is not None else [index_row()],
        truncated=False,
    )


@pytest.mark.parametrize("value", [None, "", "x" * 257])
def test_dimension_text_must_be_bounded(value: object) -> None:
    with pytest.raises(PgvectorCatalogError, match="bounded text"):
        parse_dimensions(value)  # type: ignore[arg-type]


def test_dimension_must_be_positive() -> None:
    with pytest.raises(PgvectorCatalogError, match="positive"):
        parse_dimensions("vector(0)")


def test_required_catalog_text_is_rejected_when_empty() -> None:
    malformed = identity_row()
    malformed["installed_version"] = ""

    with pytest.raises(PgvectorCatalogError, match="installed_version"):
        snapshot(identity=malformed)


def test_optional_catalog_text_is_rejected_when_not_text() -> None:
    malformed = index_row()
    malformed["predicate"] = 1

    with pytest.raises(PgvectorCatalogError, match="predicate"):
        snapshot(indexes=[malformed])


def test_required_catalog_integer_is_rejected_below_minimum() -> None:
    malformed = identity_row()
    malformed["extension_oid"] = 0

    with pytest.raises(PgvectorCatalogError, match="extension_oid"):
        snapshot(identity=malformed)


def test_missing_optional_text_arrays_are_empty() -> None:
    row = index_row()
    row["operator_classes"] = None
    row["reloptions"] = None

    result = snapshot(indexes=[row])

    assert result.indexes[0].operator_classes == ()  # type: ignore[attr-defined]
    assert result.indexes[0].options == {}  # type: ignore[attr-defined]


def test_catalog_text_arrays_reject_non_text_values() -> None:
    malformed = index_row()
    malformed["operator_classes"] = [1]

    with pytest.raises(PgvectorCatalogError, match="operator_classes"):
        snapshot(indexes=[malformed])


@pytest.mark.parametrize("reloptions", [["broken"], ["m=16", "m=32"]])
def test_relation_options_require_unique_key_value_entries(reloptions: list[str]) -> None:
    malformed = index_row()
    malformed["reloptions"] = reloptions

    with pytest.raises(PgvectorCatalogError, match="unique key=value"):
        snapshot(indexes=[malformed])


def test_unsupported_extension_owned_column_type_is_rejected() -> None:
    malformed = column_row()
    malformed["type_name"] = "future_vector"

    with pytest.raises(PgvectorCatalogError, match="not supported"):
        snapshot(columns=[malformed])


def test_column_type_must_match_formatted_catalog_type() -> None:
    malformed = column_row()
    malformed["formatted_type"] = "halfvec(3)"

    with pytest.raises(PgvectorCatalogError, match="must match"):
        snapshot(columns=[malformed])
