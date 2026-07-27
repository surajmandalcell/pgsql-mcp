# Execution safety architecture

The execution layer uses structural controls instead of prompt guidance.

## Trust boundaries

User values go to PostgreSQL as native parameters.

Identifiers come from validated catalog names and trusted identifier composition.

Public raw SQL remains read-only.

Write access uses structured tools with explicit guards.

## Read-only execution

A public read request has these controls:

- one parsed statement
- a database-enforced read-only transaction
- statement and lock timeouts
- row security
- a restricted search path
- a hard row ceiling
- a hard encoded-size ceiling
- a named server-side cursor

The cursor fetches only the row ceiling plus one row.

The extra row reports truncation.

## Atomic writes

Atomic write requests use one connection and one transaction.

Each mutation requires a maximum affected-row count.

`UPDATE` and `DELETE` also require a filter.

The service can require an exact affected-row count.

Any validation, execution, timeout, cancellation, result, or commit failure prevents a success result.

## Ambiguous commits

A connection failure during `COMMIT` creates an unknown outcome.

The server does not report success or failure when PostgreSQL state is uncertain.

## Nontransactional work

Maintenance operations use a separate reviewed domain.

The server records durable states and supports explicit reconciliation.

It does not claim rollback for commands that PostgreSQL cannot roll back.
