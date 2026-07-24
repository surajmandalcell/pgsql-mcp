"""Bounded PostgreSQL catalog and type introspection.

The catalog is PostgreSQL's source of truth for built-in, user-defined, and
extension-owned objects. These helpers intentionally avoid static type tables:
all identities and relationships are resolved by OID at request time.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from typing_extensions import LiteralString

from .runtime import ABSOLUTE_MAX_ROWS
from .runtime import DEFAULT_QUERY_TIMEOUT_SECONDS
from .sql import SqlDriver

MAX_CATALOG_ROWS = min(500, ABSOLUTE_MAX_ROWS)
MAX_SEARCH_CHARACTERS = 256

RELATION_KINDS: dict[str, str] = {
    "table": "r",
    "partitioned_table": "p",
    "view": "v",
    "materialized_view": "m",
    "sequence": "S",
    "foreign_table": "f",
    "index": "i",
    "partitioned_index": "I",
    "composite": "c",
    "toast": "t",
}

TYPE_KINDS = frozenset({"array", "base", "composite", "domain", "enum", "multirange", "pseudo", "range"})


def _checked_limit(limit: int) -> int:
    if limit <= 0:
        raise ValueError("limit must be greater than zero")
    if limit > MAX_CATALOG_ROWS:
        raise ValueError(f"limit cannot exceed {MAX_CATALOG_ROWS}")
    return limit


def _checked_offset(offset: int) -> int:
    if offset < 0:
        raise ValueError("offset cannot be negative")
    return offset


def _checked_search_term(term: str) -> str:
    normalized = term.strip()
    if not normalized:
        raise ValueError("search term must not be empty")
    if len(normalized) > MAX_SEARCH_CHARACTERS:
        raise ValueError(f"search term cannot exceed {MAX_SEARCH_CHARACTERS} characters")
    return normalized


def _checked_optional_name(value: str | None, *, label: str) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{label} must not be empty")
    if len(normalized) > MAX_SEARCH_CHARACTERS:
        raise ValueError(f"{label} cannot exceed {MAX_SEARCH_CHARACTERS} characters")
    return normalized


async def _read_rows(
    sql_driver: SqlDriver,
    query: LiteralString,
    *,
    params: list[Any] | None = None,
    max_rows: int = MAX_CATALOG_ROWS,
) -> list[dict[str, Any]]:
    result = await sql_driver.execute_bounded_query(
        query,
        params=params,
        max_rows=_checked_limit(max_rows),
        force_readonly=True,
        timeout_seconds=DEFAULT_QUERY_TIMEOUT_SECONDS,
    )
    return result.rows


async def _read_one(
    sql_driver: SqlDriver,
    query: LiteralString,
    *,
    params: list[Any] | None = None,
) -> dict[str, Any] | None:
    rows = await _read_rows(sql_driver, query, params=params, max_rows=1)
    return rows[0] if rows else None


async def get_server_info_data(sql_driver: SqlDriver) -> dict[str, Any]:
    """Return version, database, role, recovery, locale, and extension data."""
    server = await _read_one(
        sql_driver,
        """
        SELECT
            current_database() AS database,
            current_user AS current_user,
            session_user AS session_user,
            current_schema() AS current_schema,
            current_setting('server_version') AS server_version,
            current_setting('server_version_num')::integer AS server_version_num,
            current_setting('server_encoding') AS server_encoding,
            current_setting('client_encoding') AS client_encoding,
            d.datcollate AS lc_collate,
            d.datctype AS lc_ctype,
            current_setting('TimeZone') AS timezone,
            current_setting('transaction_read_only')::boolean AS transaction_read_only,
            current_setting('wal_level') AS wal_level,
            current_setting('max_connections')::integer AS max_connections,
            pg_is_in_recovery() AS in_recovery,
            version() AS version_string,
            r.rolsuper AS role_superuser,
            r.rolcreatedb AS role_create_database,
            r.rolcreaterole AS role_create_role,
            r.rolreplication AS role_replication,
            r.rolbypassrls AS role_bypass_rls
        FROM pg_catalog.pg_roles AS r
        JOIN pg_catalog.pg_database AS d ON d.datname = current_database()
        WHERE r.rolname = current_user
        """,
    )
    if server is None:
        raise RuntimeError("PostgreSQL did not return server information")

    extensions = await _read_rows(
        sql_driver,
        """
        SELECT
            e.oid,
            e.extname AS name,
            e.extversion AS version,
            n.nspname AS schema,
            e.extrelocatable AS relocatable
        FROM pg_catalog.pg_extension AS e
        JOIN pg_catalog.pg_namespace AS n ON n.oid = e.extnamespace
        ORDER BY e.extname
        """,
    )
    server["extensions"] = extensions
    return server


async def search_catalog_data(
    sql_driver: SqlDriver,
    *,
    term: str,
    schema_name: str | None = None,
    object_kind: str | None = None,
    include_system: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Search relations, routines, types, collations, and extensions."""
    normalized_term = _checked_search_term(term)
    normalized_schema = _checked_optional_name(schema_name, label="schema_name")
    normalized_kind = _checked_optional_name(object_kind, label="object_kind")
    if normalized_kind is not None:
        normalized_kind = normalized_kind.lower()
    checked_limit = _checked_limit(limit)
    checked_offset = _checked_offset(offset)
    pattern = f"%{normalized_term}%"

    return await _read_rows(
        sql_driver,
        """
        WITH catalog_objects AS (
            SELECT
                c.oid AS object_oid,
                n.nspname AS schema_name,
                c.relname AS object_name,
                CASE c.relkind
                    WHEN 'r' THEN 'table'
                    WHEN 'p' THEN 'partitioned_table'
                    WHEN 'v' THEN 'view'
                    WHEN 'm' THEN 'materialized_view'
                    WHEN 'S' THEN 'sequence'
                    WHEN 'f' THEN 'foreign_table'
                    WHEN 'i' THEN 'index'
                    WHEN 'I' THEN 'partitioned_index'
                    WHEN 'c' THEN 'composite'
                    WHEN 't' THEN 'toast'
                    ELSE 'relation'
                END AS object_kind,
                pg_catalog.obj_description(c.oid, 'pg_class') AS comment,
                NULL::text AS identity_arguments
            FROM pg_catalog.pg_class AS c
            JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
            WHERE c.relkind IN ('r', 'p', 'v', 'm', 'S', 'f', 'i', 'I', 'c', 't')

            UNION ALL

            SELECT
                p.oid,
                n.nspname,
                p.proname,
                CASE p.prokind
                    WHEN 'p' THEN 'procedure'
                    WHEN 'a' THEN 'aggregate'
                    WHEN 'w' THEN 'window_function'
                    ELSE 'function'
                END,
                pg_catalog.obj_description(p.oid, 'pg_proc'),
                pg_catalog.pg_get_function_identity_arguments(p.oid)
            FROM pg_catalog.pg_proc AS p
            JOIN pg_catalog.pg_namespace AS n ON n.oid = p.pronamespace

            UNION ALL

            SELECT
                t.oid,
                n.nspname,
                t.typname,
                CASE
                    WHEN t.typelem <> 0 AND t.typcategory = 'A' THEN 'array'
                    WHEN t.typtype = 'b' THEN 'base_type'
                    WHEN t.typtype = 'c' THEN 'composite_type'
                    WHEN t.typtype = 'd' THEN 'domain'
                    WHEN t.typtype = 'e' THEN 'enum'
                    WHEN t.typtype = 'm' THEN 'multirange'
                    WHEN t.typtype = 'p' THEN 'pseudo_type'
                    WHEN t.typtype = 'r' THEN 'range'
                    ELSE 'type'
                END,
                pg_catalog.obj_description(t.oid, 'pg_type'),
                NULL::text
            FROM pg_catalog.pg_type AS t
            JOIN pg_catalog.pg_namespace AS n ON n.oid = t.typnamespace

            UNION ALL

            SELECT
                coll.oid,
                n.nspname,
                coll.collname,
                'collation',
                pg_catalog.obj_description(coll.oid, 'pg_collation'),
                NULL::text
            FROM pg_catalog.pg_collation AS coll
            JOIN pg_catalog.pg_namespace AS n ON n.oid = coll.collnamespace

            UNION ALL

            SELECT
                e.oid,
                n.nspname,
                e.extname,
                'extension',
                pg_catalog.obj_description(e.oid, 'pg_extension'),
                NULL::text
            FROM pg_catalog.pg_extension AS e
            JOIN pg_catalog.pg_namespace AS n ON n.oid = e.extnamespace
        )
        SELECT
            object_oid,
            schema_name,
            object_name,
            object_kind,
            identity_arguments,
            comment
        FROM catalog_objects
        WHERE (object_name ILIKE %s OR COALESCE(comment, '') ILIKE %s)
          AND (%s::text IS NULL OR schema_name = %s)
          AND (%s::text IS NULL OR object_kind = %s)
          AND (
                %s
                OR (
                    schema_name NOT IN ('pg_catalog', 'information_schema')
                    AND schema_name NOT LIKE 'pg_toast%%'
                    AND schema_name NOT LIKE 'pg_temp_%%'
                )
          )
        ORDER BY schema_name, object_kind, object_name, identity_arguments NULLS FIRST
        LIMIT %s OFFSET %s
        """,
        params=[
            pattern,
            pattern,
            normalized_schema,
            normalized_schema,
            normalized_kind,
            normalized_kind,
            include_system,
            checked_limit,
            checked_offset,
        ],
        max_rows=checked_limit,
    )


async def list_relations_data(
    sql_driver: SqlDriver,
    *,
    schema_name: str | None = None,
    relation_kind: str | None = None,
    include_system: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List every PostgreSQL relation class with ownership and storage metadata."""
    normalized_schema = _checked_optional_name(schema_name, label="schema_name")
    normalized_kind = _checked_optional_name(relation_kind, label="relation_kind")
    relkind: str | None = None
    if normalized_kind is not None:
        normalized_kind = normalized_kind.lower()
        if normalized_kind not in RELATION_KINDS:
            choices = ", ".join(sorted(RELATION_KINDS))
            raise ValueError(f"unsupported relation_kind {normalized_kind!r}; expected one of: {choices}")
        relkind = RELATION_KINDS[normalized_kind]
    checked_limit = _checked_limit(limit)
    checked_offset = _checked_offset(offset)

    return await _read_rows(
        sql_driver,
        """
        SELECT
            c.oid,
            n.nspname AS schema_name,
            c.relname AS relation_name,
            CASE c.relkind
                WHEN 'r' THEN 'table'
                WHEN 'p' THEN 'partitioned_table'
                WHEN 'v' THEN 'view'
                WHEN 'm' THEN 'materialized_view'
                WHEN 'S' THEN 'sequence'
                WHEN 'f' THEN 'foreign_table'
                WHEN 'i' THEN 'index'
                WHEN 'I' THEN 'partitioned_index'
                WHEN 'c' THEN 'composite'
                WHEN 't' THEN 'toast'
                ELSE 'unknown'
            END AS relation_kind,
            pg_catalog.pg_get_userbyid(c.relowner) AS owner,
            CASE c.relpersistence
                WHEN 'p' THEN 'permanent'
                WHEN 'u' THEN 'unlogged'
                WHEN 't' THEN 'temporary'
                ELSE 'unknown'
            END AS persistence,
            c.relispartition AS is_partition,
            c.relrowsecurity AS row_security,
            c.relforcerowsecurity AS force_row_security,
            c.reltuples::bigint AS estimated_rows,
            c.relpages AS estimated_pages,
            CASE WHEN c.relkind IN ('r', 'm', 'S', 'i', 't')
                 THEN pg_catalog.pg_total_relation_size(c.oid)
                 ELSE NULL
            END AS total_size_bytes,
            ts.spcname AS tablespace,
            e.extname AS extension_name,
            pg_catalog.obj_description(c.oid, 'pg_class') AS comment
        FROM pg_catalog.pg_class AS c
        JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
        LEFT JOIN pg_catalog.pg_tablespace AS ts ON ts.oid = NULLIF(c.reltablespace, 0)
        LEFT JOIN pg_catalog.pg_depend AS d
          ON d.classid = 'pg_class'::regclass
         AND d.objid = c.oid
         AND d.deptype = 'e'
        LEFT JOIN pg_catalog.pg_extension AS e ON e.oid = d.refobjid
        WHERE c.relkind IN ('r', 'p', 'v', 'm', 'S', 'f', 'i', 'I', 'c', 't')
          AND (%s::text IS NULL OR n.nspname = %s)
          AND (%s::text IS NULL OR c.relkind = %s::"char")
          AND (
                %s
                OR (
                    n.nspname NOT IN ('pg_catalog', 'information_schema')
                    AND n.nspname NOT LIKE 'pg_toast%%'
                    AND n.nspname NOT LIKE 'pg_temp_%%'
                )
          )
        ORDER BY n.nspname, relation_kind, c.relname
        LIMIT %s OFFSET %s
        """,
        params=[
            normalized_schema,
            normalized_schema,
            relkind,
            relkind,
            include_system,
            checked_limit,
            checked_offset,
        ],
        max_rows=checked_limit,
    )


async def get_relation_details_data(
    sql_driver: SqlDriver,
    *,
    schema_name: str,
    relation_name: str,
) -> dict[str, Any]:
    """Return columns, constraints, indexes, triggers, policies, and partitions."""
    normalized_schema = _checked_optional_name(schema_name, label="schema_name")
    normalized_relation = _checked_optional_name(relation_name, label="relation_name")
    assert normalized_schema is not None
    assert normalized_relation is not None

    relation = await _read_one(
        sql_driver,
        """
        SELECT
            c.oid,
            n.nspname AS schema_name,
            c.relname AS relation_name,
            c.relkind,
            CASE c.relkind
                WHEN 'r' THEN 'table'
                WHEN 'p' THEN 'partitioned_table'
                WHEN 'v' THEN 'view'
                WHEN 'm' THEN 'materialized_view'
                WHEN 'S' THEN 'sequence'
                WHEN 'f' THEN 'foreign_table'
                WHEN 'i' THEN 'index'
                WHEN 'I' THEN 'partitioned_index'
                WHEN 'c' THEN 'composite'
                WHEN 't' THEN 'toast'
                ELSE 'unknown'
            END AS relation_kind,
            pg_catalog.pg_get_userbyid(c.relowner) AS owner,
            c.relispartition AS is_partition,
            c.relrowsecurity AS row_security,
            c.relforcerowsecurity AS force_row_security,
            c.reltuples::bigint AS estimated_rows,
            CASE WHEN c.relkind IN ('r', 'm', 'S', 'i', 't')
                 THEN pg_catalog.pg_total_relation_size(c.oid)
                 ELSE NULL
            END AS total_size_bytes,
            CASE WHEN c.relkind = 'p' THEN pg_catalog.pg_get_partkeydef(c.oid) ELSE NULL END AS partition_key,
            CASE WHEN c.relispartition THEN pg_catalog.pg_get_expr(c.relpartbound, c.oid, true) ELSE NULL END AS partition_bound,
            CASE WHEN c.relkind IN ('v', 'm') THEN pg_catalog.pg_get_viewdef(c.oid, true) ELSE NULL END AS view_definition,
            pg_catalog.obj_description(c.oid, 'pg_class') AS comment
        FROM pg_catalog.pg_class AS c
        JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
        WHERE n.nspname = %s AND c.relname = %s
        """,
        params=[normalized_schema, normalized_relation],
    )
    if relation is None:
        raise ValueError(f"relation {normalized_schema}.{normalized_relation} was not found")
    relation_oid = relation["oid"]

    columns = await _read_rows(
        sql_driver,
        """
        SELECT
            a.attnum AS ordinal_position,
            a.attname AS column_name,
            a.atttypid AS type_oid,
            tn.nspname AS type_schema,
            t.typname AS type_name,
            pg_catalog.format_type(a.atttypid, a.atttypmod) AS formatted_type,
            a.attndims AS array_dimensions,
            a.attnotnull AS not_null,
            pg_catalog.pg_get_expr(ad.adbin, ad.adrelid, true) AS default_expression,
            NULLIF(a.attidentity, '') AS identity_kind,
            NULLIF(a.attgenerated, '') AS generated_kind,
            CASE a.attstorage
                WHEN 'p' THEN 'plain'
                WHEN 'e' THEN 'external'
                WHEN 'm' THEN 'main'
                WHEN 'x' THEN 'extended'
                ELSE 'unknown'
            END AS storage,
            CASE a.attcompression
                WHEN 'p' THEN 'pglz'
                WHEN 'l' THEN 'lz4'
                ELSE NULL
            END AS compression,
            a.attstattarget AS statistics_target,
            a.attislocal AS is_local,
            a.attinhcount AS inheritance_count,
            cn.nspname AS collation_schema,
            coll.collname AS collation_name,
            pg_catalog.col_description(a.attrelid, a.attnum) AS comment
        FROM pg_catalog.pg_attribute AS a
        JOIN pg_catalog.pg_type AS t ON t.oid = a.atttypid
        JOIN pg_catalog.pg_namespace AS tn ON tn.oid = t.typnamespace
        LEFT JOIN pg_catalog.pg_attrdef AS ad
          ON ad.adrelid = a.attrelid AND ad.adnum = a.attnum
        LEFT JOIN pg_catalog.pg_collation AS coll ON coll.oid = NULLIF(a.attcollation, 0)
        LEFT JOIN pg_catalog.pg_namespace AS cn ON cn.oid = coll.collnamespace
        WHERE a.attrelid = %s
          AND a.attnum > 0
          AND NOT a.attisdropped
        ORDER BY a.attnum
        """,
        params=[relation_oid],
    )

    constraints = await _read_rows(
        sql_driver,
        """
        SELECT
            con.oid,
            con.conname AS constraint_name,
            CASE con.contype
                WHEN 'c' THEN 'check'
                WHEN 'f' THEN 'foreign_key'
                WHEN 'n' THEN 'not_null'
                WHEN 'p' THEN 'primary_key'
                WHEN 'u' THEN 'unique'
                WHEN 't' THEN 'constraint_trigger'
                WHEN 'x' THEN 'exclusion'
                ELSE 'unknown'
            END AS constraint_kind,
            con.condeferrable AS deferrable,
            con.condeferred AS initially_deferred,
            con.convalidated AS validated,
            con.connoinherit AS no_inherit,
            pg_catalog.pg_get_constraintdef(con.oid, true) AS definition,
            fn.nspname AS referenced_schema,
            fc.relname AS referenced_relation,
            ARRAY(
                SELECT a.attname
                FROM unnest(con.conkey) WITH ORDINALITY AS key(attnum, ord)
                JOIN pg_catalog.pg_attribute AS a
                  ON a.attrelid = con.conrelid AND a.attnum = key.attnum
                ORDER BY key.ord
            ) AS columns,
            ARRAY(
                SELECT a.attname
                FROM unnest(con.confkey) WITH ORDINALITY AS key(attnum, ord)
                JOIN pg_catalog.pg_attribute AS a
                  ON a.attrelid = con.confrelid AND a.attnum = key.attnum
                ORDER BY key.ord
            ) AS referenced_columns
        FROM pg_catalog.pg_constraint AS con
        LEFT JOIN pg_catalog.pg_class AS fc ON fc.oid = NULLIF(con.confrelid, 0)
        LEFT JOIN pg_catalog.pg_namespace AS fn ON fn.oid = fc.relnamespace
        WHERE con.conrelid = %s
        ORDER BY con.conname
        """,
        params=[relation_oid],
    )

    indexes = await _read_rows(
        sql_driver,
        """
        SELECT
            i.indexrelid AS index_oid,
            n.nspname AS index_schema,
            ic.relname AS index_name,
            am.amname AS access_method,
            i.indisunique AS unique,
            i.indisprimary AS primary,
            i.indisexclusion AS exclusion,
            i.indimmediate AS immediate,
            i.indisclustered AS clustered,
            i.indisvalid AS valid,
            i.indisready AS ready,
            i.indislive AS live,
            i.indisreplident AS replica_identity,
            i.indnkeyatts AS key_attribute_count,
            i.indnatts AS total_attribute_count,
            ARRAY(
                SELECT pg_catalog.pg_get_indexdef(i.indexrelid, position, true)
                FROM generate_series(1, i.indnatts) AS position
                ORDER BY position
            ) AS attributes,
            pg_catalog.pg_get_expr(i.indexprs, i.indrelid, true) AS expressions,
            pg_catalog.pg_get_expr(i.indpred, i.indrelid, false) AS predicate,
            pg_catalog.pg_get_indexdef(i.indexrelid) AS definition,
            pg_catalog.pg_relation_size(i.indexrelid) AS size_bytes,
            pg_catalog.obj_description(i.indexrelid, 'pg_class') AS comment
        FROM pg_catalog.pg_index AS i
        JOIN pg_catalog.pg_class AS ic ON ic.oid = i.indexrelid
        JOIN pg_catalog.pg_namespace AS n ON n.oid = ic.relnamespace
        JOIN pg_catalog.pg_am AS am ON am.oid = ic.relam
        WHERE i.indrelid = %s
        ORDER BY n.nspname, ic.relname
        """,
        params=[relation_oid],
    )

    triggers = await _read_rows(
        sql_driver,
        """
        SELECT
            t.oid,
            t.tgname AS trigger_name,
            CASE t.tgenabled
                WHEN 'O' THEN 'origin'
                WHEN 'D' THEN 'disabled'
                WHEN 'R' THEN 'replica'
                WHEN 'A' THEN 'always'
                ELSE 'unknown'
            END AS enabled,
            pn.nspname AS function_schema,
            p.proname AS function_name,
            pg_catalog.pg_get_triggerdef(t.oid, true) AS definition,
            pg_catalog.obj_description(t.oid, 'pg_trigger') AS comment
        FROM pg_catalog.pg_trigger AS t
        JOIN pg_catalog.pg_proc AS p ON p.oid = t.tgfoid
        JOIN pg_catalog.pg_namespace AS pn ON pn.oid = p.pronamespace
        WHERE t.tgrelid = %s AND NOT t.tgisinternal
        ORDER BY t.tgname
        """,
        params=[relation_oid],
    )

    policies = await _read_rows(
        sql_driver,
        """
        SELECT
            p.oid,
            p.polname AS policy_name,
            CASE p.polcmd
                WHEN 'r' THEN 'select'
                WHEN 'a' THEN 'insert'
                WHEN 'w' THEN 'update'
                WHEN 'd' THEN 'delete'
                WHEN '*' THEN 'all'
                ELSE 'unknown'
            END AS command,
            p.polpermissive AS permissive,
            ARRAY(
                SELECT CASE WHEN role_oid = 0 THEN 'PUBLIC' ELSE r.rolname END
                FROM unnest(p.polroles) AS role_oid
                LEFT JOIN pg_catalog.pg_roles AS r ON r.oid = role_oid
                ORDER BY CASE WHEN role_oid = 0 THEN 'PUBLIC' ELSE r.rolname END
            ) AS roles,
            pg_catalog.pg_get_expr(p.polqual, p.polrelid, true) AS using_expression,
            pg_catalog.pg_get_expr(p.polwithcheck, p.polrelid, true) AS check_expression
        FROM pg_catalog.pg_policy AS p
        WHERE p.polrelid = %s
        ORDER BY p.polname
        """,
        params=[relation_oid],
    )

    parents = await _read_rows(
        sql_driver,
        """
        SELECT
            pn.nspname AS schema_name,
            pc.relname AS relation_name
        FROM pg_catalog.pg_inherits AS i
        JOIN pg_catalog.pg_class AS pc ON pc.oid = i.inhparent
        JOIN pg_catalog.pg_namespace AS pn ON pn.oid = pc.relnamespace
        WHERE i.inhrelid = %s
        ORDER BY i.inhseqno
        """,
        params=[relation_oid],
    )

    children = await _read_rows(
        sql_driver,
        """
        SELECT
            cn.nspname AS schema_name,
            cc.relname AS relation_name,
            cc.relispartition AS is_partition,
            CASE WHEN cc.relispartition
                 THEN pg_catalog.pg_get_expr(cc.relpartbound, cc.oid, true)
                 ELSE NULL
            END AS partition_bound
        FROM pg_catalog.pg_inherits AS i
        JOIN pg_catalog.pg_class AS cc ON cc.oid = i.inhrelid
        JOIN pg_catalog.pg_namespace AS cn ON cn.oid = cc.relnamespace
        WHERE i.inhparent = %s
        ORDER BY i.inhseqno, cn.nspname, cc.relname
        """,
        params=[relation_oid],
    )

    privileges = await _read_rows(
        sql_driver,
        """
        SELECT
            grantor,
            grantee,
            privilege_type,
            is_grantable = 'YES' AS grantable
        FROM information_schema.role_table_grants
        WHERE table_schema = %s AND table_name = %s
        ORDER BY grantee, privilege_type, grantor
        """,
        params=[normalized_schema, normalized_relation],
    )

    relation.update(
        {
            "columns": columns,
            "constraints": constraints,
            "indexes": indexes,
            "triggers": triggers,
            "policies": policies,
            "parents": parents,
            "children": children,
            "privileges": privileges,
        }
    )
    return relation


async def list_postgres_types_data(
    sql_driver: SqlDriver,
    *,
    schema_name: str | None = None,
    type_kind: str | None = None,
    include_system: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List PostgreSQL types dynamically from pg_type, including extensions."""
    normalized_schema = _checked_optional_name(schema_name, label="schema_name")
    normalized_kind = _checked_optional_name(type_kind, label="type_kind")
    if normalized_kind is not None:
        normalized_kind = normalized_kind.lower()
        if normalized_kind not in TYPE_KINDS:
            choices = ", ".join(sorted(TYPE_KINDS))
            raise ValueError(f"unsupported type_kind {normalized_kind!r}; expected one of: {choices}")
    checked_limit = _checked_limit(limit)
    checked_offset = _checked_offset(offset)

    return await _read_rows(
        sql_driver,
        """
        WITH typed AS (
            SELECT
                t.oid,
                n.nspname AS schema_name,
                t.typname AS type_name,
                CASE
                    WHEN t.typelem <> 0 AND t.typcategory = 'A' THEN 'array'
                    WHEN t.typtype = 'b' THEN 'base'
                    WHEN t.typtype = 'c' THEN 'composite'
                    WHEN t.typtype = 'd' THEN 'domain'
                    WHEN t.typtype = 'e' THEN 'enum'
                    WHEN t.typtype = 'm' THEN 'multirange'
                    WHEN t.typtype = 'p' THEN 'pseudo'
                    WHEN t.typtype = 'r' THEN 'range'
                    ELSE 'unknown'
                END AS type_kind,
                t.typtype,
                t.typcategory,
                t.typispreferred AS preferred,
                pg_catalog.format_type(t.oid, NULL) AS formatted_type,
                NULLIF(t.typelem, 0) AS element_type_oid,
                NULLIF(t.typbasetype, 0) AS base_type_oid,
                NULLIF(t.typrelid, 0) AS relation_oid,
                NULLIF(t.typarray, 0) AS array_type_oid,
                t.typdelim AS delimiter,
                t.typlen AS internal_length,
                t.typbyval AS passed_by_value,
                t.typalign AS alignment,
                t.typstorage AS storage,
                t.typnotnull AS not_null,
                t.typdefault AS default_value,
                cn.nspname AS collation_schema,
                coll.collname AS collation_name,
                pg_catalog.pg_get_userbyid(t.typowner) AS owner,
                e.extname AS extension_name,
                pg_catalog.has_type_privilege(t.oid, 'USAGE') AS has_usage,
                pg_catalog.obj_description(t.oid, 'pg_type') AS comment
            FROM pg_catalog.pg_type AS t
            JOIN pg_catalog.pg_namespace AS n ON n.oid = t.typnamespace
            LEFT JOIN pg_catalog.pg_collation AS coll ON coll.oid = NULLIF(t.typcollation, 0)
            LEFT JOIN pg_catalog.pg_namespace AS cn ON cn.oid = coll.collnamespace
            LEFT JOIN pg_catalog.pg_depend AS d
              ON d.classid = 'pg_type'::regclass
             AND d.objid = t.oid
             AND d.deptype = 'e'
            LEFT JOIN pg_catalog.pg_extension AS e ON e.oid = d.refobjid
        )
        SELECT *
        FROM typed
        WHERE (%s::text IS NULL OR schema_name = %s)
          AND (%s::text IS NULL OR type_kind = %s)
          AND (
                %s
                OR (
                    schema_name NOT IN ('pg_catalog', 'information_schema')
                    AND schema_name NOT LIKE 'pg_toast%%'
                    AND schema_name NOT LIKE 'pg_temp_%%'
                )
          )
        ORDER BY schema_name, type_kind, type_name
        LIMIT %s OFFSET %s
        """,
        params=[
            normalized_schema,
            normalized_schema,
            normalized_kind,
            normalized_kind,
            include_system,
            checked_limit,
            checked_offset,
        ],
        max_rows=checked_limit,
    )


async def get_postgres_type_data(
    sql_driver: SqlDriver,
    *,
    type_oid: int | None = None,
    schema_name: str | None = None,
    type_name: str | None = None,
) -> dict[str, Any]:
    """Return a lossless OID-based description of any PostgreSQL type."""
    if type_oid is None and (schema_name is None or type_name is None):
        raise ValueError("provide type_oid or both schema_name and type_name")
    if type_oid is not None and type_oid <= 0:
        raise ValueError("type_oid must be greater than zero")
    normalized_schema = _checked_optional_name(schema_name, label="schema_name")
    normalized_name = _checked_optional_name(type_name, label="type_name")

    details = await _read_one(
        sql_driver,
        """
        SELECT
            t.oid,
            n.nspname AS schema_name,
            t.typname AS type_name,
            CASE
                WHEN t.typelem <> 0 AND t.typcategory = 'A' THEN 'array'
                WHEN t.typtype = 'b' THEN 'base'
                WHEN t.typtype = 'c' THEN 'composite'
                WHEN t.typtype = 'd' THEN 'domain'
                WHEN t.typtype = 'e' THEN 'enum'
                WHEN t.typtype = 'm' THEN 'multirange'
                WHEN t.typtype = 'p' THEN 'pseudo'
                WHEN t.typtype = 'r' THEN 'range'
                ELSE 'unknown'
            END AS type_kind,
            t.typtype,
            t.typcategory,
            t.typispreferred AS preferred,
            pg_catalog.format_type(t.oid, NULL) AS formatted_type,
            NULLIF(t.typelem, 0) AS element_type_oid,
            NULLIF(t.typbasetype, 0) AS base_type_oid,
            NULLIF(t.typrelid, 0) AS relation_oid,
            NULLIF(t.typarray, 0) AS array_type_oid,
            t.typlen AS internal_length,
            t.typbyval AS passed_by_value,
            t.typdelim AS delimiter,
            t.typalign AS alignment,
            t.typstorage AS storage,
            t.typnotnull AS not_null,
            t.typdefault AS default_value,
            t.typinput::regproc::text AS input_function,
            t.typoutput::regproc::text AS output_function,
            t.typreceive::regproc::text AS receive_function,
            t.typsend::regproc::text AS send_function,
            cn.nspname AS collation_schema,
            coll.collname AS collation_name,
            pg_catalog.pg_get_userbyid(t.typowner) AS owner,
            e.extname AS extension_name,
            pg_catalog.has_type_privilege(t.oid, 'USAGE') AS has_usage,
            pg_catalog.obj_description(t.oid, 'pg_type') AS comment
        FROM pg_catalog.pg_type AS t
        JOIN pg_catalog.pg_namespace AS n ON n.oid = t.typnamespace
        LEFT JOIN pg_catalog.pg_collation AS coll ON coll.oid = NULLIF(t.typcollation, 0)
        LEFT JOIN pg_catalog.pg_namespace AS cn ON cn.oid = coll.collnamespace
        LEFT JOIN pg_catalog.pg_depend AS d
          ON d.classid = 'pg_type'::regclass
         AND d.objid = t.oid
         AND d.deptype = 'e'
        LEFT JOIN pg_catalog.pg_extension AS e ON e.oid = d.refobjid
        WHERE (%s::oid IS NOT NULL AND t.oid = %s::oid)
           OR (%s::oid IS NULL AND n.nspname = %s AND t.typname = %s)
        ORDER BY CASE WHEN %s::oid IS NOT NULL THEN 0 ELSE 1 END
        LIMIT 1
        """,
        params=[type_oid, type_oid, type_oid, normalized_schema, normalized_name, type_oid],
    )
    if details is None:
        identifier = str(type_oid) if type_oid is not None else f"{normalized_schema}.{normalized_name}"
        raise ValueError(f"PostgreSQL type {identifier} was not found")

    oid = details["oid"]
    kind = details["type_kind"]
    details["enum_labels"] = []
    details["domain_constraints"] = []
    details["composite_attributes"] = []
    details["range"] = None

    if kind == "enum":
        details["enum_labels"] = await _read_rows(
            sql_driver,
            """
            SELECT enumlabel AS label, enumsortorder AS sort_order
            FROM pg_catalog.pg_enum
            WHERE enumtypid = %s
            ORDER BY enumsortorder
            """,
            params=[oid],
        )
    elif kind == "domain":
        details["domain_constraints"] = await _read_rows(
            sql_driver,
            """
            SELECT
                c.oid,
                c.conname AS constraint_name,
                c.convalidated AS validated,
                pg_catalog.pg_get_constraintdef(c.oid, true) AS definition
            FROM pg_catalog.pg_constraint AS c
            WHERE c.contypid = %s
            ORDER BY c.conname
            """,
            params=[oid],
        )
    elif kind == "composite":
        relation_oid = details.get("relation_oid")
        if relation_oid is not None:
            details["composite_attributes"] = await _read_rows(
                sql_driver,
                """
                SELECT
                    a.attnum AS ordinal_position,
                    a.attname AS attribute_name,
                    a.atttypid AS type_oid,
                    n.nspname AS type_schema,
                    t.typname AS type_name,
                    pg_catalog.format_type(a.atttypid, a.atttypmod) AS formatted_type,
                    a.attnotnull AS not_null,
                    a.attndims AS array_dimensions,
                    cn.nspname AS collation_schema,
                    coll.collname AS collation_name
                FROM pg_catalog.pg_attribute AS a
                JOIN pg_catalog.pg_type AS t ON t.oid = a.atttypid
                JOIN pg_catalog.pg_namespace AS n ON n.oid = t.typnamespace
                LEFT JOIN pg_catalog.pg_collation AS coll ON coll.oid = NULLIF(a.attcollation, 0)
                LEFT JOIN pg_catalog.pg_namespace AS cn ON cn.oid = coll.collnamespace
                WHERE a.attrelid = %s
                  AND a.attnum > 0
                  AND NOT a.attisdropped
                ORDER BY a.attnum
                """,
                params=[relation_oid],
            )
    elif kind in {"range", "multirange"}:
        details["range"] = await _read_one(
            sql_driver,
            """
            SELECT
                r.rngtypid AS range_type_oid,
                r.rngmultitypid AS multirange_type_oid,
                r.rngsubtype AS subtype_oid,
                n.nspname AS subtype_schema,
                t.typname AS subtype_name,
                pg_catalog.format_type(r.rngsubtype, NULL) AS formatted_subtype,
                op.opcname AS subtype_operator_class,
                am.amname AS operator_class_method,
                cn.nspname AS collation_schema,
                coll.collname AS collation_name,
                CASE WHEN r.rngcanonical::oid = 0 THEN NULL ELSE r.rngcanonical::regproc::text END AS canonical_function,
                CASE WHEN r.rngsubdiff::oid = 0 THEN NULL ELSE r.rngsubdiff::regproc::text END AS subtype_difference_function
            FROM pg_catalog.pg_range AS r
            JOIN pg_catalog.pg_type AS t ON t.oid = r.rngsubtype
            JOIN pg_catalog.pg_namespace AS n ON n.oid = t.typnamespace
            JOIN pg_catalog.pg_opclass AS op ON op.oid = r.rngsubopc
            JOIN pg_catalog.pg_am AS am ON am.oid = op.opcmethod
            LEFT JOIN pg_catalog.pg_collation AS coll ON coll.oid = NULLIF(r.rngcollation, 0)
            LEFT JOIN pg_catalog.pg_namespace AS cn ON cn.oid = coll.collnamespace
            WHERE r.rngtypid = %s OR r.rngmultitypid = %s
            """,
            params=[oid, oid],
        )

    return details


def relation_kind_from_payload(payload: Mapping[str, Any]) -> str | None:
    """Return a validated relation kind from an introspection payload."""
    value = payload.get("relation_kind")
    return value if isinstance(value, str) and value in RELATION_KINDS else None
