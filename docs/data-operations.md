# Typed guarded data operations

Structured data operations are a bounded context separate from raw SQL, migrations, catalog discovery, and maintenance. The MCP adapter accepts typed relation names, columns, predicates, ordering, values, and write guards. The PostgreSQL adapter alone composes SQL identifiers and binds every data value through psycopg.

## Tools

| Tool | Access mode | Purpose |
|---|---|---|
| `select_rows` | restricted and unrestricted | Read a bounded page with structured filters and stable keyset pagination |
| `insert_rows` | unrestricted | Insert one bounded batch with affected-row commit guards |
| `upsert_rows` | unrestricted | Insert or update through a verified non-partial primary or unique key |
| `update_rows` | unrestricted | Update a filtered set with optional optimistic-concurrency predicates |
| `delete_rows` | unrestricted | Delete a filtered set with optional optimistic-concurrency predicates |

The lite profile intentionally omits this bounded context. That exclusion is structural: the lite server does not register these tools or import the data-operations adapter.

## Domain invariants

- Relations are always represented as an exact schema and relation name. Identifiers are validated and composed with `psycopg.sql.Identifier`; they are never interpolated into SQL text.
- Filters support a bounded `(all predicates) AND (any predicates)` shape. Every value is a native bind parameter.
- Public operators are limited to equality and ordering comparisons, `IN`/`NOT IN`, `LIKE`/`ILIKE`, and explicit null checks.
- A page contains at most 500 rows and at most 512 KiB of encoded response data. A single oversized row is refused so the client can request a narrower projection.
- Keyset cursors are integrity-checked and bound to the exact relation and ordering contract. Pagination requires NOT NULL ordering columns that contain a complete primary or non-partial unique key.
- Insert and upsert batches contain at most 500 rows and use one consistent column set.
- Generated columns and generated-always identity columns cannot be inserted explicitly. Generated and identity columns cannot be updated.
- Upsert conflict columns must match a complete primary or non-partial unique key discovered from PostgreSQL's live catalogs.
- Update and delete always require a non-empty target filter. Optimistic-concurrency predicates are applied in the same `WHERE` clause.
- Every mutation requires `max_affected_rows`; `expected_rows` can require an exact count. A violation raises inside the transaction and rolls back the complete operation.
- A returning payload that exceeds the response-byte ceiling causes rollback rather than committing a mutation whose result cannot be delivered safely.

## Transaction boundary

Each call checks out one connection and opens one transaction. Reads use `REPEATABLE READ READ ONLY`; mutations use `SERIALIZABLE READ WRITE`. The adapter applies transaction-local statement, lock, and idle-transaction timeouts, enables row security, restricts the search path to `pg_catalog`, and assigns an operation-specific application name.

Expected validation and conflict failures preserve their domain error. Database errors are redacted after rollback. A connection loss during `COMMIT` is reported as an unknown commit state and must be reconciled before retrying. The adapter never retries a mutation automatically because retry safety depends on caller-owned idempotency and business semantics.

## Permissions and row-level security

The adapter verifies relation existence, supported relation kind, required table privileges, columns, generated/identity metadata, and usable unique keys through PostgreSQL catalogs before composing a statement. PostgreSQL remains the source of truth for grants, constraints, triggers, defaults, and row-level security. Operators should use a dedicated non-superuser role that does not own protected tables and does not have `BYPASSRLS`.
