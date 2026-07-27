# Testing and documentation index

Use the smallest focused test first.

Run the complete local gates before merge.

GitHub Actions verify the commit after it reaches `main`.

## Standard gates

```bash
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pytest -v
python scripts/check_ste_docs.py .
```

## Specialized gates

- Main updates require full changed-line coverage.
- The safety-kernel gate requires full statement and branch coverage.
- Mutation tests verify selected safety invariants.
- PostgreSQL 14 through PostgreSQL 18 jobs verify supported versions.
- The concurrency workflow verifies pool and cancellation behavior.
- Performance jobs verify startup, memory, package, and container budgets.
- Package and container build jobs do not publish artifacts.

## Documentation index

- [Production readiness](production-readiness.md)
- [Execution safety](architecture/execution-safety.md)
- [Catalog model](catalog.md)
- [Compatibility](compatibility.md)
- [Concurrency](concurrency.md)
- [Typed data operations](data-operations.md)
- [Extension objects](extension-objects.md)
- [Extension profiles](extensions.md)
- [PostGIS diagnostics](postgis.md)
- [pgvector diagnostics](pgvector.md)
- [Lite server](lite.md)
- [Maintenance](maintenance.md)
- [Migrations](migrations.md)
- [Observability](observability.md)
- [Performance](performance.md)
- [Provider profiles](providers.md)
- [Replication](replication.md)
- [Security](security.md)
- [ASD-STE100 project profile](asd-ste100.md)
