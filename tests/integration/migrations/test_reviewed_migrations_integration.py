"""Real PostgreSQL 15/16 contracts for reviewed atomic migrations."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from postgres_mcp.migrations import LEDGER_TABLE_NAME
from postgres_mcp.migrations import MigrationConflictError
from postgres_mcp.migrations import MigrationExecutionError
from postgres_mcp.migrations import MigrationOperationStatus
from postgres_mcp.migrations import MigrationOrderError
from postgres_mcp.migrations import MigrationPlanner
from postgres_mcp.migrations import MigrationService
from postgres_mcp.migrations import MigrationStepDraft
from postgres_mcp.migrations import PostgresMigrationBackend
from postgres_mcp.sql import DbConnPool
from postgres_mcp.sql import SqlDriver

TEST_SCHEMA = "mcp_migration_test"


@pytest_asyncio.fixture
async def migration_context(test_postgres_connection_string: tuple[str, str]) -> AsyncIterator[tuple[SqlDriver, MigrationPlanner, MigrationService]]:
    connection_string, _version = test_postgres_connection_string
    driver = SqlDriver(engine_url=connection_string)
    await driver.execute_query(f"DROP SCHEMA IF EXISTS {TEST_SCHEMA} CASCADE", force_readonly=False)
    await driver.execute_query(f"DROP TABLE IF EXISTS public.{LEDGER_TABLE_NAME}", force_readonly=False)
    await driver.execute_query(f"CREATE SCHEMA {TEST_SCHEMA}", force_readonly=False)
    planner = MigrationPlanner()
    service = MigrationService(PostgresMigrationBackend(driver))
    try:
        yield driver, planner, service
    finally:
        await driver.execute_query(f"DROP SCHEMA IF EXISTS {TEST_SCHEMA} CASCADE", force_readonly=False)
        await driver.execute_query(f"DROP TABLE IF EXISTS public.{LEDGER_TABLE_NAME}", force_readonly=False)
        if isinstance(driver.conn, DbConnPool):
            await driver.conn.close()


async def relation_exists(driver: SqlDriver, name: str) -> bool:
    rows = await driver.execute_query("SELECT pg_catalog.to_regclass(%s) IS NOT NULL AS exists", params=[name], force_readonly=True)
    return bool(rows and rows[0].cells["exists"])


@pytest.mark.asyncio
async def test_apply_and_idempotent_retry_commit_schema_and_ledger_together(migration_context) -> None:
    driver, planner, service = migration_context
    plan = planner.create_plan(
        name="001-create-items",
        steps=[
            MigrationStepDraft(
                f"CREATE TABLE {TEST_SCHEMA}.items (id bigint PRIMARY KEY, name text NOT NULL)",
                f"DROP TABLE {TEST_SCHEMA}.items",
            )
        ],
    )

    applied = await service.apply(plan, review_hash=plan.review_hash, timeout_seconds=30, lock_timeout_seconds=5)
    repeated = await service.apply(plan, review_hash=plan.review_hash, timeout_seconds=30, lock_timeout_seconds=5)
    status = await service.status(limit=10)

    assert applied.status is MigrationOperationStatus.APPLIED
    assert repeated.status is MigrationOperationStatus.ALREADY_APPLIED
    assert await relation_exists(driver, f"{TEST_SCHEMA}.items") is True
    assert [item.name for item in status.migrations] == [plan.name]
    assert status.migrations[0].review_hash == plan.review_hash


@pytest.mark.asyncio
async def test_failed_forward_step_rolls_back_schema_and_ledger(migration_context) -> None:
    driver, planner, service = migration_context
    plan = planner.create_plan(
        name="002-failed-apply",
        steps=[
            MigrationStepDraft(
                f"CREATE TABLE {TEST_SCHEMA}.atomic_items (id integer PRIMARY KEY)",
                f"DROP TABLE {TEST_SCHEMA}.atomic_items",
            ),
            MigrationStepDraft(
                f"ALTER TABLE {TEST_SCHEMA}.atomic_items ADD COLUMN id integer",
                f"ALTER TABLE {TEST_SCHEMA}.atomic_items DROP COLUMN id",
            ),
        ],
    )

    with pytest.raises(MigrationExecutionError) as error:
        await service.apply(plan, review_hash=plan.review_hash, timeout_seconds=30, lock_timeout_seconds=5)

    assert error.value.rolled_back is True
    assert error.value.failed_step == 1
    assert await relation_exists(driver, f"{TEST_SCHEMA}.atomic_items") is False
    assert (await service.status(limit=10)).migrations == ()


@pytest.mark.asyncio
async def test_reusing_name_with_changed_reviewed_content_is_rejected(migration_context) -> None:
    _driver, planner, service = migration_context
    original = planner.create_plan(
        name="003-checksum-conflict",
        steps=[MigrationStepDraft(f"CREATE TABLE {TEST_SCHEMA}.first (id integer)", f"DROP TABLE {TEST_SCHEMA}.first")],
    )
    changed = planner.create_plan(
        name=original.name,
        steps=[MigrationStepDraft(f"CREATE TABLE {TEST_SCHEMA}.second (id integer)", f"DROP TABLE {TEST_SCHEMA}.second")],
    )
    await service.apply(original, review_hash=original.review_hash, timeout_seconds=30, lock_timeout_seconds=5)

    with pytest.raises(MigrationConflictError, match="different reviewed content"):
        await service.apply(changed, review_hash=changed.review_hash, timeout_seconds=30, lock_timeout_seconds=5)


@pytest.mark.asyncio
async def test_latest_only_rollback_is_atomic_and_idempotent(migration_context) -> None:
    driver, planner, service = migration_context
    first = planner.create_plan(
        name="004-first",
        steps=[MigrationStepDraft(f"CREATE TABLE {TEST_SCHEMA}.first (id integer)", f"DROP TABLE {TEST_SCHEMA}.first")],
    )
    second = planner.create_plan(
        name="005-second",
        steps=[MigrationStepDraft(f"CREATE TABLE {TEST_SCHEMA}.second (id integer)", f"DROP TABLE {TEST_SCHEMA}.second")],
    )
    await service.apply(first, review_hash=first.review_hash, timeout_seconds=30, lock_timeout_seconds=5)
    await service.apply(second, review_hash=second.review_hash, timeout_seconds=30, lock_timeout_seconds=5)

    with pytest.raises(MigrationOrderError, match="not the latest"):
        await service.rollback(name=first.name, review_hash=first.review_hash, timeout_seconds=30, lock_timeout_seconds=5)

    rolled_back = await service.rollback(name=second.name, review_hash=second.review_hash, timeout_seconds=30, lock_timeout_seconds=5)
    repeated = await service.rollback(name=second.name, review_hash=second.review_hash, timeout_seconds=30, lock_timeout_seconds=5)

    assert rolled_back.status is MigrationOperationStatus.ROLLED_BACK
    assert repeated.status is MigrationOperationStatus.ALREADY_ROLLED_BACK
    assert await relation_exists(driver, f"{TEST_SCHEMA}.second") is False
    assert await relation_exists(driver, f"{TEST_SCHEMA}.first") is True


@pytest.mark.asyncio
async def test_failed_reverse_step_restores_prior_reverse_work_and_ledger(migration_context) -> None:
    driver, planner, service = migration_context
    plan = planner.create_plan(
        name="006-failed-rollback",
        steps=[
            MigrationStepDraft(
                f"CREATE TABLE {TEST_SCHEMA}.rollback_a (id integer)",
                f"ALTER TABLE {TEST_SCHEMA}.missing_table RENAME TO impossible",
            ),
            MigrationStepDraft(
                f"CREATE TABLE {TEST_SCHEMA}.rollback_b (id integer)",
                f"DROP TABLE {TEST_SCHEMA}.rollback_b",
            ),
        ],
    )
    await service.apply(plan, review_hash=plan.review_hash, timeout_seconds=30, lock_timeout_seconds=5)

    with pytest.raises(MigrationExecutionError) as error:
        await service.rollback(name=plan.name, review_hash=plan.review_hash, timeout_seconds=30, lock_timeout_seconds=5)

    assert error.value.rolled_back is True
    assert await relation_exists(driver, f"{TEST_SCHEMA}.rollback_a") is True
    assert await relation_exists(driver, f"{TEST_SCHEMA}.rollback_b") is True
    assert [item.name for item in (await service.status(limit=10)).migrations] == [plan.name]


@pytest.mark.asyncio
async def test_corrupted_stored_plan_blocks_rollback_and_preserves_schema(migration_context) -> None:
    driver, planner, service = migration_context
    plan = planner.create_plan(
        name="007-corrupted-ledger-plan",
        steps=[
            MigrationStepDraft(
                f"CREATE TABLE {TEST_SCHEMA}.protected_items (id integer)",
                f"DROP TABLE {TEST_SCHEMA}.protected_items",
            )
        ],
    )
    await service.apply(plan, review_hash=plan.review_hash, timeout_seconds=30, lock_timeout_seconds=5)
    await driver.execute_query(
        f"""
        UPDATE public.{LEDGER_TABLE_NAME}
        SET plan = pg_catalog.jsonb_set(
            plan,
            '{{steps,0,rollback_sql}}',
            pg_catalog.to_jsonb(%s::text),
            false
        )
        WHERE name = %s
        """,
        params=[f"DROP TABLE {TEST_SCHEMA}.different_table", plan.name],
        force_readonly=False,
    )

    with pytest.raises(MigrationConflictError, match="corrupted reviewed plan"):
        await service.rollback(
            name=plan.name,
            review_hash=plan.review_hash,
            timeout_seconds=30,
            lock_timeout_seconds=5,
        )

    assert await relation_exists(driver, f"{TEST_SCHEMA}.protected_items") is True
    assert [item.name for item in (await service.status(limit=10)).migrations] == [plan.name]


@pytest.mark.asyncio
async def test_status_payload_never_exposes_stored_forward_or_rollback_sql(migration_context) -> None:
    _driver, planner, service = migration_context
    plan = planner.create_plan(
        name="008-status-redaction",
        steps=[
            MigrationStepDraft(
                f"CREATE TABLE {TEST_SCHEMA}.status_items (id integer)",
                f"DROP TABLE {TEST_SCHEMA}.status_items",
            )
        ],
    )
    await service.apply(plan, review_hash=plan.review_hash, timeout_seconds=30, lock_timeout_seconds=5)

    payload = (await service.status(limit=10)).to_payload()

    assert payload["migrations"][0]["name"] == plan.name
    rendered = str(payload)
    assert "CREATE TABLE" not in rendered
    assert "DROP TABLE" not in rendered
