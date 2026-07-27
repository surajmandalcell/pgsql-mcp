# Test plan

This plan maps test types to release risks.

## Unit tests

Unit tests cover pure domains, parsers, policy, result encoding, and adapter failure paths.

## Integration tests

Integration tests use real PostgreSQL for transaction, catalog, migration, maintenance, replication, provider, extension, and cursor behavior.

## Version tests

The compatibility matrix runs PostgreSQL 14 through PostgreSQL 18.

## Coverage tests

Changed executable lines require full coverage.

The declared safety kernel requires full statement and branch coverage.

## Mutation tests

Mutation tests change selected safety decisions.

Every configured mutant must fail a focused test.

## Stress tests

Stress tests cover pool exhaustion, concurrent bounded reads, cancellation cleanup, and connection reuse.

## Performance tests

Performance tests enforce startup, memory, package, and container budgets.
