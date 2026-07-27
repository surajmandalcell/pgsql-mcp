# Project plan

This plan separates the verified release scope from future roadmap work.

## Verified release scope

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
- PostGIS catalog and index diagnostics.
- pgvector catalog and index diagnostics.
- Repository ASD-STE100 project profile.

## Future roadmap

1. Add TimescaleDB-specific catalog diagnostics.
2. Add Citus-specific catalog diagnostics.
3. Add resumable nontransactional migration stages.
4. Expand adapter mutation testing.
5. Increase historical-module branch coverage.

Roadmap items are not guarantees for the current release.

## Release principle

A release feature is complete only when its source passes all required local gates.

Main branch jobs verify the merged commit again.
