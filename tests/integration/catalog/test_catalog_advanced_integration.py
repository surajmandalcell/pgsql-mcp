"""Real-PostgreSQL coverage for catalog objects and every dynamic type family."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from postgres_mcp.catalog_advanced import get_postgres_type_data
from postgres_mcp.catalog_advanced import get_relation_details_data
from postgres_mcp.catalog_advanced import get_server_info_data
from postgres_mcp.catalog_advanced import list_postgres_types_data
from postgres_mcp.catalog_advanced import list_relations_data
from postgres_mcp.catalog_advanced import search_catalog_data
from postgres_mcp.sql import DbConnPool
from postgres_mcp.sql import SqlDriver

TEST_SCHEMA = "mcp_catalog_test"


@pytest_asyncio.fixture
async def catalog_driver(test_postgres_connection_string: tuple[str, str]) -> AsyncIterator[SqlDriver]:
    """Create a real driver and a catalog-rich disposable schema."""
    connection_string, _version = test_postgres_connection_string
    driver = SqlDriver(engine_url=connection_string)
    await driver.execute_query(f"DROP SCHEMA IF EXISTS {TEST_SCHEMA} CASCADE", force_readonly=False)
    await driver.execute_query(f"CREATE SCHEMA {TEST_SCHEMA}", force_readonly=False)
    try:
        await driver.execute_query(
            f"""
            CREATE TYPE {TEST_SCHEMA}.mood AS ENUM ('sad', 'ok', 'happy');

            CREATE DOMAIN {TEST_SCHEMA}.positive_integer AS integer
                CHECK (VALUE > 0);

            CREATE TYPE {TEST_SCHEMA}.postal_address AS (
                street text,
                postal_code integer
            );

            CREATE TYPE {TEST_SCHEMA}.price_range AS RANGE (
                subtype = numeric,
                multirange_type_name = {TEST_SCHEMA}.price_multirange
            );

            CREATE SEQUENCE {TEST_SCHEMA}.external_sequence;

            CREATE TABLE {TEST_SCHEMA}.items (
                id bigint GENERATED ALWAYS AS IDENTITY,
                tenant_id integer NOT NULL,
                mood {TEST_SCHEMA}.mood NOT NULL,
                positive_value {TEST_SCHEMA}.positive_integer NOT NULL,
                address {TEST_SCHEMA}.postal_address,
                tags text[] NOT NULL DEFAULT ARRAY[]::text[],
                active_span int4range,
                prices {TEST_SCHEMA}.price_multirange,
                payload jsonb NOT NULL DEFAULT '{{}}'::jsonb,
                generated_value integer GENERATED ALWAYS AS (tenant_id + positive_value) STORED,
                CONSTRAINT items_pkey PRIMARY KEY (id),
                CONSTRAINT tenant_positive CHECK (tenant_id > 0)
            ) PARTITION BY RANGE (id);

            CREATE TABLE {TEST_SCHEMA}.items_p1
                PARTITION OF {TEST_SCHEMA}.items
                FOR VALUES FROM (0) TO (1000);

            CREATE INDEX items_tenant_value_idx
                ON {TEST_SCHEMA}.items (tenant_id, positive_value)
                INCLUDE (mood)
                WHERE positive_value > 0;

            ALTER TABLE {TEST_SCHEMA}.items ENABLE ROW LEVEL SECURITY;
            CREATE POLICY tenant_policy ON {TEST_SCHEMA}.items
                FOR ALL
                USING (tenant_id = current_setting('app.tenant_id', true)::integer)
                WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::integer);

            CREATE FUNCTION {TEST_SCHEMA}.touch_payload()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            BEGIN
                NEW.payload := COALESCE(NEW.payload, '{{}}'::jsonb) || jsonb_build_object('touched', true);
                RETURN NEW;
            END
            $$;

            CREATE TRIGGER touch_payload_before_write
                BEFORE INSERT OR UPDATE ON {TEST_SCHEMA}.items
                FOR EACH ROW EXECUTE FUNCTION {TEST_SCHEMA}.touch_payload();

            CREATE VIEW {TEST_SCHEMA}.item_view AS
                SELECT id, tenant_id, mood FROM {TEST_SCHEMA}.items;

            CREATE MATERIALIZED VIEW {TEST_SCHEMA}.item_summary AS
                SELECT tenant_id, count(*) AS item_count
                FROM {TEST_SCHEMA}.items
                GROUP BY tenant_id
                WITH NO DATA;

            CREATE FUNCTION {TEST_SCHEMA}.identity_value(value integer)
            RETURNS integer
            LANGUAGE sql
            IMMUTABLE
            AS 'SELECT value';
            """,
            force_readonly=False,
        )
        yield driver
    finally:
        await driver.execute_query(f"DROP SCHEMA IF EXISTS {TEST_SCHEMA} CASCADE", force_readonly=False)
        if isinstance(driver.conn, DbConnPool):
            await driver.conn.close()


@pytest.mark.asyncio
async def test_server_info_and_catalog_search(catalog_driver: SqlDriver) -> None:
    server = await get_server_info_data(catalog_driver)
    assert server["database"]
    assert server["server_version_num"] >= 140000
    assert server["current_user"]
    assert isinstance(server["extensions"], list)

    found = await search_catalog_data(catalog_driver, term="item", schema_name=TEST_SCHEMA, limit=100)
    identities = {(row["object_kind"], row["object_name"]) for row in found}
    assert ("partitioned_table", "items") in identities
    assert ("table", "items_p1") in identities
    assert ("view", "item_view") in identities
    assert ("materialized_view", "item_summary") in identities

    functions = await search_catalog_data(
        catalog_driver,
        term="identity",
        schema_name=TEST_SCHEMA,
        object_kind="function",
        limit=100,
    )
    assert {(row["object_kind"], row["object_name"]) for row in functions} == {("function", "identity_value")}


@pytest.mark.asyncio
async def test_all_relation_classes_and_details(catalog_driver: SqlDriver) -> None:
    relations = await list_relations_data(catalog_driver, schema_name=TEST_SCHEMA, limit=100)
    by_name = {row["relation_name"]: row for row in relations}

    assert by_name["items"]["relation_kind"] == "partitioned_table"
    assert by_name["items_p1"]["relation_kind"] == "table"
    assert by_name["items_p1"]["is_partition"] is True
    assert by_name["item_view"]["relation_kind"] == "view"
    assert by_name["item_summary"]["relation_kind"] == "materialized_view"
    assert by_name["external_sequence"]["relation_kind"] == "sequence"

    details = await get_relation_details_data(catalog_driver, schema_name=TEST_SCHEMA, relation_name="items")
    assert details["relation_kind"] == "partitioned_table"
    assert "RANGE" in details["partition_key"]
    assert details["row_security"] is True
    assert {child["relation_name"] for child in details["children"]} == {"items_p1"}

    columns = {column["column_name"]: column for column in details["columns"]}
    assert columns["id"]["identity_kind"] == "a"
    assert columns["generated_value"]["generated_kind"] == "s"
    assert columns["mood"]["type_schema"] == TEST_SCHEMA
    assert columns["mood"]["type_name"] == "mood"
    assert isinstance(columns["mood"]["type_oid"], int)
    assert columns["tags"]["array_dimensions"] == 1

    assert any(constraint["constraint_kind"] == "primary_key" for constraint in details["constraints"])
    predicates = [index["predicate"] for index in details["indexes"] if index["predicate"]]
    assert any("positive_value" in predicate and ">" in predicate and "0" in predicate for predicate in predicates)
    assert any(trigger["trigger_name"] == "touch_payload_before_write" for trigger in details["triggers"])
    assert any(policy["policy_name"] == "tenant_policy" for policy in details["policies"])


@pytest.mark.asyncio
async def test_every_postgres_type_family_is_discovered_by_oid(catalog_driver: SqlDriver) -> None:
    types = await list_postgres_types_data(catalog_driver, schema_name=TEST_SCHEMA, limit=100)
    by_name = {row["type_name"]: row for row in types}

    assert by_name["mood"]["type_kind"] == "enum"
    assert by_name["positive_integer"]["type_kind"] == "domain"
    assert by_name["postal_address"]["type_kind"] == "composite"
    assert by_name["price_range"]["type_kind"] == "range"
    assert by_name["price_multirange"]["type_kind"] == "multirange"
    assert by_name["_mood"]["type_kind"] == "array"
    assert by_name["_postal_address"]["type_kind"] == "array"

    enum = await get_postgres_type_data(catalog_driver, type_oid=by_name["mood"]["oid"])
    assert [entry["label"] for entry in enum["enum_labels"]] == ["sad", "ok", "happy"]

    domain = await get_postgres_type_data(
        catalog_driver,
        schema_name=TEST_SCHEMA,
        type_name="positive_integer",
    )
    assert domain["base_type_oid"] == 23
    assert any("VALUE > 0" in item["definition"] for item in domain["domain_constraints"])

    composite = await get_postgres_type_data(catalog_driver, type_oid=by_name["postal_address"]["oid"])
    assert [attribute["attribute_name"] for attribute in composite["composite_attributes"]] == ["street", "postal_code"]

    range_type = await get_postgres_type_data(catalog_driver, type_oid=by_name["price_range"]["oid"])
    multirange_type = await get_postgres_type_data(catalog_driver, type_oid=by_name["price_multirange"]["oid"])
    assert range_type["range"]["subtype_name"] == "numeric"
    assert range_type["range"]["multirange_type_oid"] == by_name["price_multirange"]["oid"]
    assert multirange_type["range"]["range_type_oid"] == by_name["price_range"]["oid"]

    array = await get_postgres_type_data(catalog_driver, type_oid=by_name["_mood"]["oid"])
    assert array["type_kind"] == "array"
    assert array["element_type_oid"] == by_name["mood"]["oid"]
