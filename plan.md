# Project plan

This plan records the remaining product work.

## Completed foundations

- Safe bounded SQL execution.
- Guarded atomic transactions.
- Restricted lite server.
- OID-backed catalog and type inspection.
- Reviewed transactional migrations.
- Typed data operations.
- PostgreSQL 14 through PostgreSQL 18 compatibility.
- Blocking coverage and mutation gates.
- Reviewed nontransactional maintenance.
- Performance and container budgets.
- Replication and failover diagnostics.
- Runtime observability.
- Server-side bounded cursors.
- Extension and provider profiles.
- Pool and cancellation stress gates.
- Generic extension object inventory.

## Active priorities

1. Apply ASD-STE100 style to all repository Markdown.
2. Add read-only pgvector catalog and index diagnostics.
3. Add PostGIS catalog contracts.
4. Add TimescaleDB catalog contracts.
5. Add Citus catalog contracts.
6. Add resumable nontransactional migration stages.
7. Expand adapter mutation testing.
8. Increase historical-module branch coverage.

## Release principle

A feature is complete only when its source-only head passes all required gates.
