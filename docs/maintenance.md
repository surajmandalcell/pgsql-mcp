# Reviewed maintenance

Reviewed maintenance covers PostgreSQL commands that cannot use the migration transaction model.

## Supported operations

- `VACUUM ANALYZE`
- `ANALYZE`
- `REINDEX INDEX CONCURRENTLY`
- `REFRESH MATERIALIZED VIEW CONCURRENTLY`

## Review model

A plan binds the operation, target identity, live target OID, catalog preconditions, options, warnings, and review hash.

The service verifies the review hash before execution.

It rechecks live catalog state immediately before execution.

## Execution

Maintenance runs in autocommit mode.

The backend applies session timeouts, row security, a restricted search path, and a target advisory lock.

## Durable states

The ledger records:

- `running`
- `succeeded`
- `failed`
- `unknown`
- reconciled terminal states

A connection loss or status-write uncertainty creates an unknown outcome.

An operator must verify the database state before reconciliation.

## Rollback

Maintenance has no rollback API.

The server does not claim rollback for nontransactional PostgreSQL commands.
