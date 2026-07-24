# PostgreSQL catalog and type introspection

The full `pgsql-mcp` profile reads PostgreSQL's live system catalogs rather than maintaining a static list of object or data types. This is important for domains, composite types, ranges, multiranges, arrays, extension-owned types, and future server versions whose OIDs are assigned by PostgreSQL.

## Catalog tools

| Tool | Purpose |
|---|---|
| `get_server_info` | Report server version, database, role capabilities, recovery state, locale, encoding, WAL mode, and installed extensions |
| `search_catalog` | Search relations, routines, types, collations, and extensions by name or comment |
| `list_relations` | List tables, partitioned tables, partitions, views, materialized views, sequences, foreign tables, indexes, composite relations, and TOAST relations |
| `get_relation_details` | Inspect columns, OIDs, defaults, identity/generated state, constraints, indexes, triggers, RLS policies, inheritance, partitions, and grants |
| `list_postgres_types` | List built-in, user-defined, and extension-owned types from `pg_type` |
| `get_postgres_type` | Inspect one type by OID or schema-qualified name |

All catalog SQL is internal, parameterized, database-enforced read-only, timed, and row-bounded. User input is passed only as values; it is never interpolated as an identifier or SQL fragment.

## Type families

The type tools discover these families dynamically:

- base and pseudo types;
- arrays and their element OIDs;
- enums and ordered labels;
- domains, base types, defaults, nullability, and constraints;
- composites and ordered attributes;
- ranges and multiranges, including subtype, operator class, collation, canonical function, and subtype-difference function;
- extension-owned and user-defined types that the project has never seen before.

Query result column metadata includes `type_oid` whenever psycopg supplies a numeric PostgreSQL type code. Clients can pass that OID to `get_postgres_type` to resolve the exact server-side identity. Values that ordinary JSON cannot represent without loss continue to use the tagged JSON fallback.

## Compatibility

The queries use catalog fields present in supported PostgreSQL releases and are exercised against the repository's real PostgreSQL matrix. Capability detection and OID relationships are preferred over vendor-name checks, so managed PostgreSQL services and extensions can expose the subset their server actually supports.
