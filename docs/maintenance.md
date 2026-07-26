# Reviewed nontransactional maintenance

Maintenance is a separate bounded context from raw SQL, typed data operations, and transactional migrations. It covers PostgreSQL operations whose effects cannot be honestly represented as one rollback-guaranteed transaction.

## Workflow

1. Call `create_maintenance_plan` with a stable name, structured operation, and exact schema-qualified target.
2. Review the live target OID, relation kind, persistence, preconditions, options, warnings, and `review_hash`.
3. Call `apply_maintenance_plan` with the exact same request and review hash.
4. Inspect `get_maintenance_status` for redacted durable state.
5. When an execution outcome is `unknown`, verify PostgreSQL externally and call `reconcile_maintenance_operation` with the exact review hash and explicit outcome.

Planning and status are available in restricted mode. Apply and reconciliation require `--access-mode=unrestricted` and a least-privilege role with only the required maintenance privileges. The reliability-focused `pgsql-mcp-lite` profile intentionally omits every maintenance tool and does not import this bounded context.

## Supported operations

- `vacuum_analyze`
- `analyze`
- `reindex_index_concurrently`
- `refresh_materialized_view_concurrently`

There is no raw SQL parameter and no maintenance rollback tool. The planner validates operation-specific relation kinds and options, binds the plan to the live target OID and catalog preconditions, and refuses temporary targets, concurrent exclusion-index reindex, unpopulated concurrent refresh, and concurrent refresh without a usable all-row unique index.

## Durable state and locking

Each apply uses a session-level advisory lock scoped to the reviewed target OID. The durable ledger records `running`, `succeeded`, `failed`, or `unknown` state. A cancellation, timeout, connection loss, or status-write failure after execution begins can produce an unknown outcome; callers must reconcile rather than blindly retry.

An exact replay of a successfully recorded review hash is idempotent and returns the stored success without executing PostgreSQL again. This is important for operations such as concurrent reindexing, which can legitimately replace the physical index OID after the reviewed operation succeeds.

The adapter validates ledger ownership, persistence, relation kind, columns, RLS state, triggers, and rules before trusting stored plans. Status payloads expose identifiers, hashes, operation, target, timestamps, state, and a redacted error code—never raw SQL or database exception details.

## Transaction semantics

PostgreSQL requires `VACUUM` outside a transaction block. Concurrent reindex and concurrent materialized-view refresh also have operation-specific nontransactional behavior. The adapter therefore uses an autocommit session, explicit session cleanup, bounded timeouts, target locking, durable state, and reconciliation rather than claiming rollback safety.
