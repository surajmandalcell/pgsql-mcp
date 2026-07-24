"""Portable PostgreSQL catalog queries shared by server profiles."""

from __future__ import annotations

from typing import Any

from .sql import SafeSqlDriver
from .sql import SqlDriver

DatabaseDriver = SqlDriver | SafeSqlDriver


async def list_schemas_data(sql_driver: DatabaseDriver) -> list[dict[str, Any]]:
    """Return schemas with a stable user/system classification."""
    rows = await sql_driver.execute_query(
        """
        SELECT
            schema_name,
            schema_owner,
            CASE
                WHEN schema_name LIKE 'pg_%' THEN 'System Schema'
                WHEN schema_name = 'information_schema' THEN 'System Information Schema'
                ELSE 'User Schema'
            END AS schema_type
        FROM information_schema.schemata
        ORDER BY schema_type, schema_name
        """
    )
    return [row.cells for row in rows] if rows else []


async def list_objects_data(
    sql_driver: DatabaseDriver,
    *,
    schema_name: str,
    object_type: str,
) -> list[dict[str, Any]]:
    """Return tables, views, sequences, or extensions."""
    if object_type in ("table", "view"):
        table_type = "BASE TABLE" if object_type == "table" else "VIEW"
        rows = await SafeSqlDriver.execute_param_query(
            sql_driver,
            """
            SELECT
                t.table_schema,
                t.table_name,
                t.table_type,
                obj_description(
                    (quote_ident(t.table_schema) || '.' || quote_ident(t.table_name))::regclass,
                    'pg_class'
                ) AS comment
            FROM information_schema.tables t
            WHERE t.table_schema = {} AND t.table_type = {}
            ORDER BY t.table_name
            """,
            [schema_name, table_type],
        )
        return [
            {
                "schema": row.cells["table_schema"],
                "name": row.cells["table_name"],
                "type": row.cells["table_type"],
                "comment": row.cells["comment"],
            }
            for row in rows or []
        ]
    if object_type == "sequence":
        rows = await SafeSqlDriver.execute_param_query(
            sql_driver,
            """
            SELECT sequence_schema, sequence_name, data_type
            FROM information_schema.sequences
            WHERE sequence_schema = {}
            ORDER BY sequence_name
            """,
            [schema_name],
        )
        return [
            {
                "schema": row.cells["sequence_schema"],
                "name": row.cells["sequence_name"],
                "data_type": row.cells["data_type"],
            }
            for row in rows or []
        ]
    if object_type == "extension":
        rows = await sql_driver.execute_query(
            """
            SELECT extname, extversion, extrelocatable
            FROM pg_extension
            ORDER BY extname
            """
        )
        return [
            {
                "name": row.cells["extname"],
                "version": row.cells["extversion"],
                "relocatable": row.cells["extrelocatable"],
            }
            for row in rows or []
        ]
    raise ValueError(f"Unsupported object type: {object_type}")


async def get_object_details_data(
    sql_driver: DatabaseDriver,
    *,
    schema_name: str,
    object_name: str,
    object_type: str,
) -> dict[str, Any]:
    """Return stable details for a table, view, sequence, or extension."""
    if object_type in ("table", "view"):
        column_rows = await SafeSqlDriver.execute_param_query(
            sql_driver,
            """
            SELECT
                c.column_name,
                c.data_type,
                c.is_nullable,
                c.column_default,
                col_description(
                    (quote_ident(c.table_schema) || '.' || quote_ident(c.table_name))::regclass,
                    c.ordinal_position
                ) AS comment
            FROM information_schema.columns c
            WHERE c.table_schema = {} AND c.table_name = {}
            ORDER BY c.ordinal_position
            """,
            [schema_name, object_name],
        )
        constraint_rows = await SafeSqlDriver.execute_param_query(
            sql_driver,
            """
            SELECT tc.constraint_name, tc.constraint_type, kcu.column_name
            FROM information_schema.table_constraints AS tc
            LEFT JOIN information_schema.key_column_usage AS kcu
              ON tc.constraint_name = kcu.constraint_name
             AND tc.table_schema = kcu.table_schema
            WHERE tc.table_schema = {} AND tc.table_name = {}
            """,
            [schema_name, object_name],
        )
        index_rows = await SafeSqlDriver.execute_param_query(
            sql_driver,
            """
            SELECT indexname, indexdef
            FROM pg_indexes
            WHERE schemaname = {} AND tablename = {}
            """,
            [schema_name, object_name],
        )
        comment_rows = await SafeSqlDriver.execute_param_query(
            sql_driver,
            """
            SELECT obj_description(
                (quote_ident({}) || '.' || quote_ident({}))::regclass,
                'pg_class'
            ) AS comment
            """,
            [schema_name, object_name],
        )

        constraints: dict[str, dict[str, Any]] = {}
        for row in constraint_rows or []:
            name = row.cells["constraint_name"]
            entry = constraints.setdefault(
                name,
                {"type": row.cells["constraint_type"], "columns": []},
            )
            if row.cells["column_name"]:
                entry["columns"].append(row.cells["column_name"])

        return {
            "basic": {
                "schema": schema_name,
                "name": object_name,
                "type": object_type,
                "comment": comment_rows[0].cells["comment"] if comment_rows else None,
            },
            "columns": [
                {
                    "column": row.cells["column_name"],
                    "data_type": row.cells["data_type"],
                    "is_nullable": row.cells["is_nullable"],
                    "default": row.cells["column_default"],
                    "comment": row.cells["comment"],
                }
                for row in column_rows or []
            ],
            "constraints": [{"name": name, **data} for name, data in constraints.items()],
            "indexes": [{"name": row.cells["indexname"], "definition": row.cells["indexdef"]} for row in index_rows or []],
        }
    if object_type == "sequence":
        rows = await SafeSqlDriver.execute_param_query(
            sql_driver,
            """
            SELECT sequence_schema, sequence_name, data_type, start_value, increment
            FROM information_schema.sequences
            WHERE sequence_schema = {} AND sequence_name = {}
            """,
            [schema_name, object_name],
        )
        if not rows:
            return {}
        row = rows[0]
        return {
            "schema": row.cells["sequence_schema"],
            "name": row.cells["sequence_name"],
            "data_type": row.cells["data_type"],
            "start_value": row.cells["start_value"],
            "increment": row.cells["increment"],
        }
    if object_type == "extension":
        rows = await SafeSqlDriver.execute_param_query(
            sql_driver,
            """
            SELECT extname, extversion, extrelocatable
            FROM pg_extension
            WHERE extname = {}
            """,
            [object_name],
        )
        if not rows:
            return {}
        row = rows[0]
        return {
            "name": row.cells["extname"],
            "version": row.cells["extversion"],
            "relocatable": row.cells["extrelocatable"],
        }
    raise ValueError(f"Unsupported object type: {object_type}")
