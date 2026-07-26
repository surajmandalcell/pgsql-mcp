# PostgreSQL compatibility

`pgsql-mcp` treats PostgreSQL capabilities and live catalogs as the source of truth. Version support is an executable release contract rather than a documentation-only claim.

## Supported majors

The blocking compatibility matrix currently covers PostgreSQL 14, 15, 16, 17, and 18. These are the upstream-supported major releases as of July 2026. PostgreSQL 14 reaches upstream end of life on November 12, 2026; the matrix and this document must be updated together when the supported window changes.

A major version is advertised only after all blocking compatibility jobs pass the same real-database contracts. End-of-life releases are best effort and must not prevent security, parser, catalog, or transaction improvements for supported releases.

## What the matrix validates

Each major runs the following bounded-context integration contracts against a real disposable PostgreSQL server:

- OID-backed catalog discovery, relation metadata, dynamic PostgreSQL types, partitions, policies, triggers, views, and materialized views;
- typed guarded data operations, keyset pagination, generated-column protections, optimistic concurrency, affected-row rollback, and response-byte limits;
- reviewed migration planning, atomic application, trusted-ledger validation, idempotence, conflicts, latest-only rollback, redaction, and failed-step rollback;
- reviewed nontransactional maintenance planning, live target revalidation, advisory locking, durable status, exact-major behavior, and explicit unknown-outcome reconciliation.

The ordinary pull-request suite retains PostgreSQL 15 and 16 as its fast pair. The dedicated matrix independently selects one image per job through `PGSQL_MCP_TEST_POSTGRES_IMAGE`, preventing accidental cross-version reuse.

## Local reproduction

Run one supported matrix cell with Docker available:

```bash
PGSQL_MCP_TEST_POSTGRES_IMAGE=postgres:14 uv run pytest -v \
  tests/unit/test_postgres_matrix.py \
  tests/integration/catalog \
  tests/integration/data_ops \
  tests/integration/migrations \
  tests/integration/maintenance
```

Accepted values are `postgres:14` through `postgres:18`. Unknown, preview, and end-of-life image values fail during test collection instead of silently weakening coverage.

## Deployment tiers

### Guaranteed baseline

Self-hosted PostgreSQL over TCP or Unix sockets, including containers, virtual machines, and Kubernetes, is the baseline contract when the server major is in the supported matrix.

### Managed PostgreSQL

Managed services are expected to work when they preserve the required PostgreSQL catalogs, privileges, SQL, and transaction semantics. Provider restrictions are discovered through capabilities and database errors; provider-specific administration features are not assumed.

### PostgreSQL-compatible products

Wire-compatible products such as distributed SQL databases require separate adapters and contract suites. They are not described as fully supported PostgreSQL merely because they accept the PostgreSQL protocol.

## Extension tiers

The test image installs `pg_stat_statements` and HypoPG because workload and index-analysis paths use them. PostGIS, TimescaleDB, Citus, pgvector, and other extension ecosystems need dedicated fixtures and compatibility profiles before the project describes them as fully tested.

## Adding or removing a major

1. Update `SUPPORTED_POSTGRES_IMAGES` in `tests/utils.py`.
2. Update the matrix in `.github/workflows/postgres-compatibility.yml`.
3. Run the complete ordinary suite and all supported-major jobs.
4. Resolve differences through capability detection or catalog feature checks where possible.
5. Update this document and the README only after every blocking job is green.

Upstream lifecycle dates are maintained at <https://www.postgresql.org/support/versioning/>.
