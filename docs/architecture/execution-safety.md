# Execution safety architecture

## Design goals

The execution layer is intentionally small and dependency-light. PostgreSQL remains responsible for SQL semantics and transaction isolation; pgsql-mcp adds conservative request validation, policy selection, result bounds, lifecycle cleanup, and stable MCP responses.

## Components

### `runtime.py`

Defines the single source of truth for access modes, server profiles, pool defaults, query timeouts, and result ceilings. `QueryLimits` validates configuration before the MCP transport starts.

### `sql/transaction.py`

Contains pure request models and pre-execution validation. It:

- normalizes psycopg positional placeholders only for parser inspection;
- requires placeholder and parameter counts to match;
- parses exactly one statement with pglast;
- permits a deliberately small transaction statement set;
- rejects transaction control, DDL, locking selects, `SELECT INTO`, and data-modifying CTEs;
- applies mutation row guards before any connection is acquired.

No database I/O occurs in this module, which keeps safety decisions deterministic and unit-testable.

### `sql/query_guard.py`

Combines single-statement validation with the existing deep read-only AST validator. The original SQL and values are never reconstructed from the validation copy. Execution is delegated to the bounded driver inside a client-side timeout.

### `sql/sql_driver.py`

Owns connection lifecycle and database transaction boundaries. Public bounded execution and atomic transactions use connection-level `commit()` and `rollback()` so cleanup does not depend on another SQL statement succeeding. Bounded reads apply transaction-local statement, lock, idle-in-transaction, row-security, and search-path settings before user SQL runs.

Atomic transaction invariants:

1. all steps are validated first;
2. one connection is acquired;
3. one `BEGIN` is issued with a trusted enum-derived isolation level;
4. transaction-local statement, lock, and idle-in-transaction timeouts, row security, and search path are applied;
5. each mutation must report a reliable affected-row count;
6. row guards are checked before commit;
7. cancellation is caught long enough to roll back and is then propagated;
8. success is constructed only after commit returns.

### `sql/results.py`

Defines bounded result envelopes and loss-aware serialization. The codec keeps ordinary JSON compact while tagging values that would otherwise lose precision or type intent.

## Compatibility boundary

The existing `SqlDriver.execute_query()` method remains for internal health, EXPLAIN, and tuning modules whose queries are authored by the project. User-supplied SQL must use `execute_bounded_query()` through the server tool. New features must not expose the unbounded compatibility method to MCP callers.

## Review checklist

Changes to the execution path must verify:

- parser failure is fail-closed;
- placeholders remain separate from SQL values;
- no new statement shape bypasses mutation guards;
- every exit path commits exactly once or rolls back;
- cancellation and timeout paths clean up the transaction;
- row limits are enforced before response construction;
- error responses do not claim commit after a failed commit;
- restricted mode remains the default;
- advanced dependencies are not added to the hot path without measurement.
