# Mutation testing

Statement and branch coverage prove that tests execute code paths; mutation testing verifies that those tests reject meaningful behavioral changes.

## Blocking safety kernel

The initial blocking mutation contract covers three deterministic application-policy modules:

- `src/postgres_mcp/runtime.py`
- `src/postgres_mcp/migrations/service.py`
- `src/postgres_mcp/data_ops/service.py`

These modules define access policy, reviewed-migration orchestration, and typed-data orchestration. Every generated mutant in the selected, covered code must be killed. Surviving, untested, suspicious, timed-out, crashed, or unchecked mutants fail CI.

The scope is deliberately bounded. PostgreSQL adapters and transport code need fault-injection and real-database mutation fixtures before they can be added without producing misleading or excessively slow results.

## Reproduce locally

Run on Linux, macOS, or another platform with process-fork support:

```bash
uv sync --frozen --all-extras
rm -rf mutants
uv run --with 'mutmut==3.6.0' mutmut run
uv run --with 'mutmut==3.6.0' mutmut results
```

Configuration lives in `setup.cfg`. Mutmut is installed ephemerally by `uv`; it is not a runtime dependency and does not alter the lockfile.

## Expanding the contract

A module may enter the blocking set only when:

1. Its ordinary unit and integration contracts are deterministic.
2. Its line and branch coverage is already complete.
3. Its mutation run has no surviving or indeterminate mutants.
4. The focused run stays within the CI execution budget.
5. Equivalent mutations are documented narrowly rather than hidden with broad exclusions.

The long-term target is mutation enforcement for SQL validation, transactions, reviewed migrations, typed data operations, reviewed maintenance, authorization, and release-budget policy.
