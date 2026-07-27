# Reviewed migrations

Reviewed migrations separate planning from execution.

## Plan structure

A plan contains ordered forward and rollback steps.

Each step contains exactly one PostgreSQL statement.

The plan also contains classifications, warnings, checksums, and a review hash.

## Policy

The planner permits supported transactional schema changes.

It rejects forbidden or nontransactional operations.

The service rechecks policy when it applies or rolls back a stored plan.

## Apply

The backend uses one serializable transaction and one advisory lock.

DDL and the ledger row commit in the same transaction.

An exact successful replay is idempotent.

Changed content under an existing migration name is an error.

## Rollback

Only the latest migration can roll back.

Rollback statements run in reverse order.

Rollback SQL and ledger removal use one transaction.

## Unknown commit state

A connection failure during `COMMIT` returns an unknown commit state.

The server does not guess the database result.
