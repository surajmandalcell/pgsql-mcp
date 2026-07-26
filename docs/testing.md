# Testing and quality gates

The test strategy follows the project’s bounded contexts. Domain contracts are fast and deterministic; PostgreSQL adapters are validated against disposable real servers; MCP adapters verify access policy and stable payloads; release workflows enforce formatting, static typing, coverage, and compatibility.

## Required suites

- Unit tests cover pure domain invariants, application-service delegation, SQL composition, failure translation, rollback, cancellation, and commit ambiguity.
- Integration tests exercise catalog, transaction, migration, and typed-data behavior against real PostgreSQL servers.
- The ordinary pull-request workflow runs the complete suite with PostgreSQL 15 and 16.
- The compatibility workflow runs the catalog, typed-data, and migration contracts independently on every advertised PostgreSQL major.

## Coverage model

Coverage is measured with statement and branch coverage over `postgres_mcp`. Reports are produced in terminal, XML, JSON, and HTML-compatible formats. A separate report isolates the safety-critical execution, migration, and typed-data packages.

Changed-line coverage is compared with the pull request’s base branch. The target contract is:

- 100% changed-line coverage for first-party Python changes;
- 100% statement and branch coverage for transaction, authorization, migration, and structured-data safety modules;
- no unreviewed coverage exclusions;
- explicit tests for success, validation rejection, authorization rejection, timeout, cancellation, rollback, database failure, cleanup, and ambiguous commit state for every mutating tool.

The initial diagnostic workflow records the exact baseline before thresholds become blocking. Thresholds must be raised through tests rather than lowered to accommodate uncovered behavior.

## Local commands

```bash
uv sync --all-extras
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pytest -v
uv run --with 'coverage[toml]>=7.6,<8' coverage run --branch --source=postgres_mcp -m pytest
uv run --with 'coverage[toml]>=7.6,<8' coverage report -m
```

Run one PostgreSQL compatibility cell with:

```bash
PGSQL_MCP_TEST_POSTGRES_IMAGE=postgres:18 uv run pytest -v \
  tests/integration/catalog \
  tests/integration/data_ops \
  tests/integration/migrations
```

## Mutation testing

Mutation tests are introduced only for deterministic safety modules and use focused test selections. A surviving mutation is treated as a missing behavioral assertion, not as a reason to exclude code. Database/network adapters remain covered by integration and fault-injection contracts where mutation execution would be nondeterministic or prohibitively slow.
