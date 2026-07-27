# pgvector catalog diagnostics

The `get_pgvector_diagnostics` tool reads pgvector metadata. It does not execute pgvector functions.

The tool uses PostgreSQL core catalogs only. It uses bounded, read-only queries.

## Reported columns

The tool reports columns that use pgvector-owned types.

It reports these types:

- `vector`
- `halfvec`
- `sparsevec`
- `bit`

The tool reads the dimension from `pg_catalog.format_type`. An unconstrained type has no reported dimension.

## Reported indexes

The tool reports indexes that use pgvector access methods or operator classes.

It reports these access methods:

- `hnsw`
- `ivfflat`

It preserves unknown operator classes. This behavior supports future pgvector versions.

The result includes index validity, readiness, predicates, expressions, definitions, and relation options.

## Findings

The tool reports an error for an invalid index.

The tool reports a warning for an index that is not ready.

The tool reports information for a partial index or an unknown operator class.

These findings describe catalog state only. They do not measure recall, latency, or result quality.

## Limits

The combined column and index limit is 500 items.

The tool rejects malformed rows and duplicate object identities.

The tool does not change indexes, data, settings, or extension state.

## Test image

The PostgreSQL integration image installs pgvector v0.8.2.

The PostgreSQL 14 through 18 matrix runs the pgvector catalog contract.
