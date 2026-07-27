# Testing and documentation index

Use the smallest focused test first.

Run the complete gates before merge.

## Standard gates

```bash
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pytest -v
python scripts/check_ste_docs.py .
```

## Specialized gates

- Changed-line coverage checks each pull request.
- The safety-kernel gate requires full statement and branch coverage.
- Mutation tests verify selected safety invariants.
- PostgreSQL 14 through PostgreSQL 18 jobs verify supported versions.
- The concurrency workflow verifies pool and cancellation behavior.
- Performance jobs verify startup, memory, package, and container budgets.

## Documentation index

- [Execution safety](architecture/execution-safety.md)
- [Catalog model](catalog.md)
- [Compatibility](compatibility.md)
- [Concurrency](concurrency.md)
- [Typed data operations](data-operations.md)
- [Extension objects](extension-objects.md)
- [Extension profiles](extensions.md)
- [Lite server](lite.md)
- [Maintenance](maintenance.md)
- [Migrations](migrations.md)
- [Observability](observability.md)
- [Performance](performance.md)
- [Provider profiles](providers.md)
- [Replication](replication.md)
- [Security](security.md)
- [ASD-STE100 style](asd-ste100.md)
