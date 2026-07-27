"""Contracts for generic PostgreSQL extension-owned object discovery."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from postgres_mcp.extension_objects import MAX_EXTENSION_OBJECTS
from postgres_mcp.extension_objects import ExtensionObjectError
from postgres_mcp.extension_objects import PostgresExtensionObjectRepository
from postgres_mcp.extension_objects import validate_extension_name
from postgres_mcp.sql import BoundedQueryResult


def extension_row() -> dict[str, object]:
    return {
        "extension_oid": 123,
        "name": "vector",
        "installed_version": "0.8.0",
        "schema_name": "extensions",
        "relocatable": True,
    }


def object_row(
    *,
    object_type: str = "type",
    schema_name: str | None = "extensions",
    object_name: str | None = "vector",
    identity: str = "extensions.vector",
    catalog_name: str = "pg_type",
    object_oid: int = 456,
    object_sub_id: int = 0,
) -> dict[str, object]:
    return {
        "object_type": object_type,
        "schema_name": schema_name,
        "object_name": object_name,
        "identity": identity,
        "catalog_name": catalog_name,
        "object_oid": object_oid,
        "object_sub_id": object_sub_id,
    }


def result(
    rows: list[dict[str, object]],
    *,
    truncated: bool = False,
) -> BoundedQueryResult:
    return BoundedQueryResult(
        rows=rows,
        columns=[],
        row_count=len(rows),
        truncated=truncated,
        affected_rows=None,
        command="SELECT",
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("vector", "vector"),
        ("  PLPGSQL ", "plpgsql"),
        ("name-with-dash", "name-with-dash"),
    ],
)
def test_extension_name_normalization(value: str, expected: str) -> None:
    assert validate_extension_name(value) == expected


@pytest.mark.parametrize("value", ["", "   ", "x" * 64, "bad\x00name"])
def test_invalid_extension_names_are_rejected(value: str) -> None:
    with pytest.raises(ExtensionObjectError):
        validate_extension_name(value)


@pytest.mark.asyncio
async def test_repository_binds_extension_identity_and_returns_bounded_objects() -> None:
    driver = AsyncMock()
    driver.execute_bounded_query.side_effect = [
        result([extension_row()]),
        result(
            [
                object_row(),
                object_row(
                    object_type="function",
                    object_name="vector_in",
                    identity="extensions.vector_in(cstring,oid,integer)",
                    catalog_name="pg_proc",
                    object_oid=457,
                ),
            ],
            truncated=True,
        ),
    ]
    repository = PostgresExtensionObjectRepository(driver, timeout_seconds=4)

    snapshot = await repository.snapshot(" VECTOR ", limit=2)

    assert snapshot.extension.to_payload() == {
        "oid": 123,
        "name": "vector",
        "installed_version": "0.8.0",
        "schema": "extensions",
        "relocatable": True,
    }
    payload = snapshot.to_payload()
    assert payload["object_count"] == 2
    assert payload["object_types"] == {"function": 1, "type": 1}
    assert payload["truncated"] is True
    assert payload["objects"][0]["catalog"] == "pg_type"

    first, second = driver.execute_bounded_query.await_args_list
    assert first.kwargs == {
        "params": ["vector"],
        "max_rows": 1,
        "force_readonly": True,
        "timeout_seconds": 4,
    }
    assert second.kwargs == {
        "params": [123],
        "max_rows": 2,
        "force_readonly": True,
        "timeout_seconds": 4,
    }
    normalized_sql = " ".join(second.args[0].split()).lower()
    assert "pg_depend" in normalized_sql
    assert "pg_identify_object" in normalized_sql
    assert "deptype = 'e'" in normalized_sql


@pytest.mark.asyncio
async def test_missing_extension_has_stable_error() -> None:
    driver = AsyncMock()
    driver.execute_bounded_query.return_value = result([])
    repository = PostgresExtensionObjectRepository(driver)

    with pytest.raises(ExtensionObjectError, match="is not installed"):
        await repository.snapshot("missing")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "extension_result",
    [
        result([extension_row()], truncated=True),
        result([extension_row(), extension_row()]),
    ],
)
async def test_extension_identity_must_be_exactly_one_row(extension_result: BoundedQueryResult) -> None:
    driver = AsyncMock()
    driver.execute_bounded_query.return_value = extension_result
    repository = PostgresExtensionObjectRepository(driver)

    with pytest.raises(ExtensionObjectError, match="exactly one row"):
        await repository.snapshot("vector")


@pytest.mark.asyncio
async def test_duplicate_object_addresses_are_rejected() -> None:
    driver = AsyncMock()
    driver.execute_bounded_query.side_effect = [
        result([extension_row()]),
        result([object_row(), object_row(identity="same address")]),
    ]
    repository = PostgresExtensionObjectRepository(driver)

    with pytest.raises(ExtensionObjectError, match="duplicate"):
        await repository.snapshot("vector")


@pytest.mark.parametrize("limit", [0, MAX_EXTENSION_OBJECTS + 1])
@pytest.mark.asyncio
async def test_limit_is_bounded(limit: int) -> None:
    repository = PostgresExtensionObjectRepository(AsyncMock())
    with pytest.raises(ExtensionObjectError, match="limit"):
        await repository.snapshot("vector", limit=limit)


def test_repository_timeout_must_be_positive() -> None:
    with pytest.raises(ValueError, match="positive"):
        PostgresExtensionObjectRepository(AsyncMock(), timeout_seconds=0)


@pytest.mark.parametrize(
    "malformed",
    [
        {**extension_row(), "extension_oid": 0},
        {**extension_row(), "relocatable": 1},
        {**extension_row(), "installed_version": ""},
        {**extension_row(), "schema_name": "x" * 64},
    ],
)
@pytest.mark.asyncio
async def test_malformed_extension_catalog_rows_are_rejected(malformed: dict[str, object]) -> None:
    driver = AsyncMock()
    driver.execute_bounded_query.return_value = result([malformed])
    repository = PostgresExtensionObjectRepository(driver)

    with pytest.raises(ExtensionObjectError):
        await repository.snapshot("vector")


@pytest.mark.parametrize(
    "malformed",
    [
        object_row(object_type=""),
        object_row(identity=""),
        object_row(catalog_name=""),
        object_row(object_oid=0),
        object_row(object_sub_id=-1),
        object_row(schema_name="x" * 64),
    ],
)
@pytest.mark.asyncio
async def test_malformed_object_catalog_rows_are_rejected(malformed: dict[str, object]) -> None:
    driver = AsyncMock()
    driver.execute_bounded_query.side_effect = [result([extension_row()]), result([malformed])]
    repository = PostgresExtensionObjectRepository(driver)

    with pytest.raises(ExtensionObjectError):
        await repository.snapshot("vector")
