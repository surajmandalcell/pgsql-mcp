# Reviewed atomic migrations

Reviewed migrations are a separate bounded context from raw SQL execution. The planner is pure and database-independent; the application service verifies the reviewed aggregate; the PostgreSQL adapter owns transaction, locking, and ledger invariants.

## Workflow

1. Call `create_migration_plan` with a stable name and ordered forward/rollback statement pairs.
2. Review the normalized statement classifications, warnings, checksum, and `review_hash`.
3. Call `apply_migration_plan` with the exact same inputs and review hash.
4. Inspect `get_migration_status` for redacted ledger metadata.
5. Use `rollback_migration` only for the latest applied migration and the exact stored review hash.

Planning and status are available in restricted mode. Apply and rollback require `--access-mode=unrestricted` and a database role with only the DDL permissions the reviewed plan needs.

Each apply or rollback request is a single bounded operation. The server does not expose long-lived migration transaction handles, so a client disconnect cannot leave a deliberately pinned migration session waiting for a later command.

## Domain invariants

- A plan contains one to 100 steps.
- Each forward and rollback entry contains exactly one PostgreSQL statement.
- The checksum binds ordered forward execution content.
- The review hash uses a separate hash domain and binds forward SQL, rollback SQL, classifications, order, and warnings.
- Aggregate integrity is recomputed before database access.
- The atomic executor accepts only statements classified as fully transactional in both directions.
- Concurrent index operations, `VACUUM`, database-global operations, temporary objects, materialized-view creation, procedural code, extensions, foreign servers, and other external or non-rollback effects are refused by the atomic executor.
- DDL and its ledger row commit on one connection and one `SERIALIZABLE` transaction.
- A transaction-scoped advisory lock serializes migration writers.
- Statement, lock, and idle-transaction timeouts are set locally.
- The ledger schema, relation kind, persistence, ownership, columns, RLS state, triggers, and rules are verified before ledger data is trusted.
- Reapplying identical reviewed content is idempotent; reusing a name with different reviewed content is a conflict.
- Rollback executes stored, integrity-checked compensation in reverse order and only for the latest migration.
- A connection loss during `COMMIT` is reported as an unknown commit state rather than falsely claiming rollback.

## Trusted ledger

The default ledger is `public._postgres_mcp_migrations`. Select an existing schema with `--migration-schema` or `MIGRATION_SCHEMA`. The identifier must be a single unquoted PostgreSQL identifier.

The ledger stores the canonical reviewed plan as JSONB, but status responses expose only identifiers, hashes, version, batch, step count, actor, and timestamp. Forward and rollback SQL are not returned by the status tool.

## Non-transactional changes

The first public migration executor intentionally refuses operations that PostgreSQL cannot safely represent as one rollback-guaranteed transaction. Concurrent index builds and other non-transactional maintenance need a separate plan/review/apply/status workflow with resumable state and compensating guidance; they must not be smuggled into this API.
