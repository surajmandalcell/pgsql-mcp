"""Schema introspection for pulling database schema."""

import logging
from dataclasses import dataclass
from dataclasses import field
from typing import Optional

from ..sql import SqlDriver

logger = logging.getLogger(__name__)


@dataclass
class ColumnInfo:
    """Information about a database column."""

    name: str
    data_type: str
    is_nullable: bool
    column_default: Optional[str]
    character_maximum_length: Optional[int]
    numeric_precision: Optional[int]
    numeric_scale: Optional[int]
    is_identity: bool
    identity_generation: Optional[str]
    comment: Optional[str] = None


@dataclass
class ConstraintInfo:
    """Information about a database constraint."""

    name: str
    constraint_type: str  # PRIMARY KEY, FOREIGN KEY, UNIQUE, CHECK
    columns: list[str]
    foreign_table_schema: Optional[str] = None
    foreign_table_name: Optional[str] = None
    foreign_columns: Optional[list[str]] = None
    check_clause: Optional[str] = None
    on_update: Optional[str] = None
    on_delete: Optional[str] = None


@dataclass
class IndexInfo:
    """Information about a database index."""

    name: str
    columns: list[str]
    is_unique: bool
    is_primary: bool
    index_type: str  # btree, hash, gin, gist, etc.
    definition: str


@dataclass
class TableInfo:
    """Information about a database table."""

    schema: str
    name: str
    columns: list[ColumnInfo] = field(default_factory=list)
    constraints: list[ConstraintInfo] = field(default_factory=list)
    indexes: list[IndexInfo] = field(default_factory=list)
    comment: Optional[str] = None


@dataclass
class SequenceInfo:
    """Information about a database sequence."""

    schema: str
    name: str
    data_type: str
    start_value: int
    increment: int
    min_value: int
    max_value: int
    cycle: bool


@dataclass
class ViewInfo:
    """Information about a database view."""

    schema: str
    name: str
    definition: str
    is_materialized: bool = False


@dataclass
class EnumInfo:
    """Information about a database enum type."""

    schema: str
    name: str
    values: list[str]


@dataclass
class SchemaInfo:
    """Complete schema information."""

    tables: list[TableInfo] = field(default_factory=list)
    views: list[ViewInfo] = field(default_factory=list)
    sequences: list[SequenceInfo] = field(default_factory=list)
    enums: list[EnumInfo] = field(default_factory=list)


class SchemaPull:
    """Pull schema information from a PostgreSQL database."""

    def __init__(self, sql_driver: SqlDriver):
        """Initialize the schema puller.

        Args:
            sql_driver: SQL driver for database access
        """
        self.sql_driver = sql_driver

    async def pull_schema(self, schemas: list[str] | None = None) -> SchemaInfo:
        """Pull complete schema information from the database.

        Args:
            schemas: List of schema names to pull (default: ['public'])

        Returns:
            SchemaInfo with all schema objects
        """
        if schemas is None:
            schemas = ["public"]

        schema_info = SchemaInfo()

        for schema in schemas:
            tables = await self.pull_tables(schema)
            schema_info.tables.extend(tables)

            views = await self.pull_views(schema)
            schema_info.views.extend(views)

            sequences = await self.pull_sequences(schema)
            schema_info.sequences.extend(sequences)

            enums = await self.pull_enums(schema)
            schema_info.enums.extend(enums)

        return schema_info

    async def pull_tables(self, schema: str = "public") -> list[TableInfo]:
        """Pull all tables from a schema.

        Args:
            schema: Schema name

        Returns:
            List of TableInfo objects
        """
        # Get tables
        tables_query = """
        SELECT
            t.table_schema,
            t.table_name,
            obj_description(
                (quote_ident(t.table_schema) || '.' || quote_ident(t.table_name))::regclass,
                'pg_class'
            ) AS comment
        FROM information_schema.tables t
        WHERE t.table_schema = %s
        AND t.table_type = 'BASE TABLE'
        ORDER BY t.table_name
        """
        rows = await self.sql_driver.execute_query(tables_query, params=[schema])
        if not rows:
            return []

        tables = []
        for row in rows:
            table_name = row.cells["table_name"]
            table = TableInfo(
                schema=schema,
                name=table_name,
                comment=row.cells["comment"],
            )

            # Get columns
            table.columns = await self._pull_columns(schema, table_name)

            # Get constraints
            table.constraints = await self._pull_constraints(schema, table_name)

            # Get indexes
            table.indexes = await self._pull_indexes(schema, table_name)

            tables.append(table)

        return tables

    async def _pull_columns(self, schema: str, table: str) -> list[ColumnInfo]:
        """Pull column information for a table."""
        query = """
        SELECT
            c.column_name,
            c.data_type,
            c.is_nullable = 'YES' AS is_nullable,
            c.column_default,
            c.character_maximum_length,
            c.numeric_precision,
            c.numeric_scale,
            c.is_identity = 'YES' AS is_identity,
            c.identity_generation,
            col_description(
                (quote_ident(c.table_schema) || '.' || quote_ident(c.table_name))::regclass,
                c.ordinal_position
            ) AS comment
        FROM information_schema.columns c
        WHERE c.table_schema = %s
        AND c.table_name = %s
        ORDER BY c.ordinal_position
        """
        rows = await self.sql_driver.execute_query(query, params=[schema, table])
        if not rows:
            return []

        return [
            ColumnInfo(
                name=row.cells["column_name"],
                data_type=row.cells["data_type"],
                is_nullable=row.cells["is_nullable"],
                column_default=row.cells["column_default"],
                character_maximum_length=row.cells["character_maximum_length"],
                numeric_precision=row.cells["numeric_precision"],
                numeric_scale=row.cells["numeric_scale"],
                is_identity=row.cells["is_identity"],
                identity_generation=row.cells["identity_generation"],
                comment=row.cells["comment"],
            )
            for row in rows
        ]

    async def _pull_constraints(self, schema: str, table: str) -> list[ConstraintInfo]:
        """Pull constraint information for a table."""
        query = """
        SELECT
            tc.constraint_name,
            tc.constraint_type,
            array_agg(kcu.column_name ORDER BY kcu.ordinal_position) AS columns,
            ccu.table_schema AS foreign_table_schema,
            ccu.table_name AS foreign_table_name,
            array_agg(DISTINCT ccu.column_name) FILTER (WHERE ccu.column_name IS NOT NULL) AS foreign_columns,
            rc.update_rule AS on_update,
            rc.delete_rule AS on_delete,
            cc.check_clause
        FROM information_schema.table_constraints tc
        LEFT JOIN information_schema.key_column_usage kcu
            ON tc.constraint_name = kcu.constraint_name
            AND tc.table_schema = kcu.table_schema
        LEFT JOIN information_schema.constraint_column_usage ccu
            ON tc.constraint_name = ccu.constraint_name
            AND tc.table_schema = ccu.constraint_schema
            AND tc.constraint_type = 'FOREIGN KEY'
        LEFT JOIN information_schema.referential_constraints rc
            ON tc.constraint_name = rc.constraint_name
            AND tc.table_schema = rc.constraint_schema
        LEFT JOIN information_schema.check_constraints cc
            ON tc.constraint_name = cc.constraint_name
            AND tc.table_schema = cc.constraint_schema
        WHERE tc.table_schema = %s
        AND tc.table_name = %s
        GROUP BY
            tc.constraint_name,
            tc.constraint_type,
            ccu.table_schema,
            ccu.table_name,
            rc.update_rule,
            rc.delete_rule,
            cc.check_clause
        ORDER BY tc.constraint_name
        """
        rows = await self.sql_driver.execute_query(query, params=[schema, table])
        if not rows:
            return []

        return [
            ConstraintInfo(
                name=row.cells["constraint_name"],
                constraint_type=row.cells["constraint_type"],
                columns=row.cells["columns"] or [],
                foreign_table_schema=row.cells["foreign_table_schema"],
                foreign_table_name=row.cells["foreign_table_name"],
                foreign_columns=row.cells["foreign_columns"],
                on_update=row.cells["on_update"],
                on_delete=row.cells["on_delete"],
                check_clause=row.cells["check_clause"],
            )
            for row in rows
        ]

    async def _pull_indexes(self, schema: str, table: str) -> list[IndexInfo]:
        """Pull index information for a table."""
        query = """
        SELECT
            i.relname AS index_name,
            array_agg(a.attname ORDER BY array_position(ix.indkey, a.attnum)) AS columns,
            ix.indisunique AS is_unique,
            ix.indisprimary AS is_primary,
            am.amname AS index_type,
            pg_get_indexdef(ix.indexrelid) AS definition
        FROM pg_index ix
        JOIN pg_class t ON t.oid = ix.indrelid
        JOIN pg_class i ON i.oid = ix.indexrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        JOIN pg_am am ON am.oid = i.relam
        JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = ANY(ix.indkey)
        WHERE n.nspname = %s
        AND t.relname = %s
        GROUP BY i.relname, ix.indisunique, ix.indisprimary, am.amname, ix.indexrelid
        ORDER BY i.relname
        """
        rows = await self.sql_driver.execute_query(query, params=[schema, table])
        if not rows:
            return []

        return [
            IndexInfo(
                name=row.cells["index_name"],
                columns=row.cells["columns"] or [],
                is_unique=row.cells["is_unique"],
                is_primary=row.cells["is_primary"],
                index_type=row.cells["index_type"],
                definition=row.cells["definition"],
            )
            for row in rows
        ]

    async def pull_views(self, schema: str = "public") -> list[ViewInfo]:
        """Pull all views from a schema."""
        query = """
        SELECT
            v.table_schema AS schema,
            v.table_name AS name,
            pg_get_viewdef(
                (quote_ident(v.table_schema) || '.' || quote_ident(v.table_name))::regclass,
                true
            ) AS definition,
            m.relkind = 'm' AS is_materialized
        FROM information_schema.views v
        LEFT JOIN pg_class m
            ON m.relname = v.table_name
            AND m.relnamespace = (SELECT oid FROM pg_namespace WHERE nspname = v.table_schema)
        WHERE v.table_schema = %s
        ORDER BY v.table_name
        """
        rows = await self.sql_driver.execute_query(query, params=[schema])
        if not rows:
            return []

        return [
            ViewInfo(
                schema=row.cells["schema"],
                name=row.cells["name"],
                definition=row.cells["definition"],
                is_materialized=row.cells["is_materialized"] or False,
            )
            for row in rows
        ]

    async def pull_sequences(self, schema: str = "public") -> list[SequenceInfo]:
        """Pull all sequences from a schema."""
        query = """
        SELECT
            s.sequence_schema AS schema,
            s.sequence_name AS name,
            s.data_type,
            s.start_value::bigint,
            s.increment::bigint,
            s.minimum_value::bigint AS min_value,
            s.maximum_value::bigint AS max_value,
            s.cycle_option = 'YES' AS cycle
        FROM information_schema.sequences s
        WHERE s.sequence_schema = %s
        ORDER BY s.sequence_name
        """
        rows = await self.sql_driver.execute_query(query, params=[schema])
        if not rows:
            return []

        return [
            SequenceInfo(
                schema=row.cells["schema"],
                name=row.cells["name"],
                data_type=row.cells["data_type"],
                start_value=row.cells["start_value"],
                increment=row.cells["increment"],
                min_value=row.cells["min_value"],
                max_value=row.cells["max_value"],
                cycle=row.cells["cycle"],
            )
            for row in rows
        ]

    async def pull_enums(self, schema: str = "public") -> list[EnumInfo]:
        """Pull all enum types from a schema."""
        query = """
        SELECT
            n.nspname AS schema,
            t.typname AS name,
            array_agg(e.enumlabel ORDER BY e.enumsortorder) AS values
        FROM pg_type t
        JOIN pg_namespace n ON n.oid = t.typnamespace
        JOIN pg_enum e ON e.enumtypid = t.oid
        WHERE n.nspname = %s
        AND t.typtype = 'e'
        GROUP BY n.nspname, t.typname
        ORDER BY t.typname
        """
        rows = await self.sql_driver.execute_query(query, params=[schema])
        if not rows:
            return []

        return [
            EnumInfo(
                schema=row.cells["schema"],
                name=row.cells["name"],
                values=row.cells["values"] or [],
            )
            for row in rows
        ]

    def generate_create_table_sql(self, table: TableInfo) -> str:
        """Generate CREATE TABLE SQL from TableInfo.

        Args:
            table: TableInfo object

        Returns:
            CREATE TABLE SQL statement
        """
        lines = []
        lines.append(f'CREATE TABLE "{table.schema}"."{table.name}" (')

        # Columns
        column_defs = []
        for col in table.columns:
            col_def = f'    "{col.name}" {col.data_type}'
            if col.character_maximum_length:
                col_def = f'    "{col.name}" {col.data_type}({col.character_maximum_length})'
            if not col.is_nullable:
                col_def += " NOT NULL"
            if col.column_default and not col.is_identity:
                col_def += f" DEFAULT {col.column_default}"
            if col.is_identity:
                gen = col.identity_generation or "BY DEFAULT"
                col_def += f" GENERATED {gen} AS IDENTITY"
            column_defs.append(col_def)

        # Primary key constraint
        for constraint in table.constraints:
            if constraint.constraint_type == "PRIMARY KEY":
                cols = ", ".join(f'"{c}"' for c in constraint.columns)
                column_defs.append(f'    CONSTRAINT "{constraint.name}" PRIMARY KEY ({cols})')

        lines.append(",\n".join(column_defs))
        lines.append(");")

        # Add other constraints
        for constraint in table.constraints:
            if constraint.constraint_type == "FOREIGN KEY":
                cols = ", ".join(f'"{c}"' for c in constraint.columns)
                ref_cols = ", ".join(f'"{c}"' for c in (constraint.foreign_columns or []))
                ref_table = f'"{constraint.foreign_table_schema}"."{constraint.foreign_table_name}"'
                fk_sql = f'ALTER TABLE "{table.schema}"."{table.name}" ADD CONSTRAINT "{constraint.name}" '
                fk_sql += f"FOREIGN KEY ({cols}) REFERENCES {ref_table} ({ref_cols})"
                if constraint.on_update:
                    fk_sql += f" ON UPDATE {constraint.on_update}"
                if constraint.on_delete:
                    fk_sql += f" ON DELETE {constraint.on_delete}"
                fk_sql += ";"
                lines.append(fk_sql)
            elif constraint.constraint_type == "UNIQUE":
                cols = ", ".join(f'"{c}"' for c in constraint.columns)
                lines.append(f'ALTER TABLE "{table.schema}"."{table.name}" ADD CONSTRAINT "{constraint.name}" UNIQUE ({cols});')
            elif constraint.constraint_type == "CHECK" and constraint.check_clause:
                lines.append(f'ALTER TABLE "{table.schema}"."{table.name}" ADD CONSTRAINT "{constraint.name}" CHECK ({constraint.check_clause});')

        # Add indexes (excluding primary key)
        for index in table.indexes:
            if not index.is_primary:
                lines.append(f"{index.definition};")

        # Add comment
        if table.comment:
            escaped_comment = table.comment.replace("'", "''")
            lines.append(f'COMMENT ON TABLE "{table.schema}"."{table.name}" IS \'{escaped_comment}\';')

        # Add column comments
        for col in table.columns:
            if col.comment:
                escaped_comment = col.comment.replace("'", "''")
                lines.append(f'COMMENT ON COLUMN "{table.schema}"."{table.name}"."{col.name}" IS \'{escaped_comment}\';')

        return "\n".join(lines)
