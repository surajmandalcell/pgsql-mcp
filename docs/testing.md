# Testing and quality gates

The test strategy follows the project’s bounded contexts. Domain contracts are fast and deterministic; PostgreSQL adapters are validated against disposable real servers; MCP adapters verify access policy and stable payloads; release workflows enforce formatting, static typing, coverage, compatibility, mutation strength, and measured runtime budgets.

## Required suites

- Unit tests cover pure domain invariants, application-service delegation, SQL composition, failure translation, rollback, cancellation, and commit ambiguity.
- Integration tests exercise catalog, transaction, migration, typed-data, and reviewed-maintenance behavior against real PostgreSQL servers.
- The ordinary pull-request workflow runs the complete suite with PostgreSQL 15 and 16.
- The compatibility workflow runs the catalog, typed-data, migration, and maintenance contracts independently on every advertised PostgreSQL major.

## Coverage model

Coverage is measured with statement and branch coverage over `postgres_mcp`. Reports are produced in terminal, XML, JSON, and HTML-compatible formats. A separate report isolates the safety-critical execution, migration, and typed-data packages.

Changed-line coverage is compared with the pull request’s base branch. The blocking contract is:

- 100% changed-line coverage for first-party Python changes;
- 100% statement and branch coverage for the declared transaction, authorization, migration, and structured-data safety kernel;
- no unreviewed coverage exclusions;
- explicit tests for success, validation rejection, authorization rejection, timeout, cancellation, rollback, database failure, cleanup, and ambiguous commit state for every mutating tool.

Thresholds are raised through tests rather than lowered to accommodate uncovered behavior. Historical modules remain visible in the complete coverage report until their bounded contexts are modernized.

## Mutation testing

The deterministic mutation gate physically changes safety-critical source code one semantic fault at a time. It first runs a clean focused baseline, requires every source target to match exactly once, executes each mutant in a fresh bytecode cache, restores the source in a `finally` block, and accepts only an ordinary pytest failure as proof that the mutant was killed. A passing mutant is a release failure; collection errors, timeouts, and other infrastructure failures are also release failures rather than false positives.

The initial blocking catalog covers 13 invariants across:

- runtime timeout and absolute-row ceilings;
- public read-only statement and session-mutation rejection;
- data-modifying CTE, `WHERE`, and affected-row transaction guards;
- lossless JSON-safe-integer boundary encoding;
- reviewed migration hashes, transactional applicability, and timeout ordering;
- reviewed maintenance constant-time hash verification and timeout ordering.

Mutation targets are intentionally explicit and reviewable. New deterministic safety invariants should add a focused mutation and a test that kills it. Database/network adapters remain covered by real-PostgreSQL integration and fault-injection contracts where source mutation would be nondeterministic or prohibitively slow.

## Local commands

```bash
uv sync --all-extras
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pytest -v
uv run --with 'coverage[toml]>=7.6,<8' coverage run --branch --source=postgres_mcp -m pytest
uv run --with 'coverage[toml]>=7.6,<8' coverage report -m
uv run python tests/mutation/safety_mutations.py --output mutation-results.json
```

List or run selected mutations with:

```bash
uv run python tests/mutation/safety_mutations.py --list
uv run python tests/mutation/safety_mutations.py \
  --mutation migration_requires_exact_review_hash \
  --mutation maintenance_requires_constant_time_review_hash_match
```

Run one PostgreSQL compatibility cell with:

```bash
PGSQL_MCP_TEST_POSTGRES_IMAGE=postgres:18 uv run pytest -v \
  tests/integration/catalog \
  tests/integration/data_ops \
  tests/integration/migrations \
  tests/integration/maintenance
```
