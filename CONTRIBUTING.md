# Contributing

pgsql-mcp is maintained with a lightweight lifecycle suitable for a single maintainer while preserving the reviewability expected of an open-source database tool.

## Development lifecycle

### 1. Frame the change

Write the problem, user impact, non-goals, safety implications, and acceptance criteria before implementation. For a small fix, the pull-request description is sufficient. For architecture or security work, add or update a document under `docs/architecture/`.

### 2. Establish the baseline

Reproduce the defect or record the current behavior. Add a failing regression test when practical. For performance work, record the command, dataset, PostgreSQL version, and baseline measurement.

### 3. Work on a focused branch

Use one branch and pull request per independently reviewable concern. Keep generated files, unrelated formatting, dependency churn, and drive-by refactors out of the diff.

### 4. Implement from the boundary inward

For database changes, define validation and transaction invariants before adding MCP surface area. Keep SQL values parameterized, identifiers composed safely, results bounded, and optional features lazy-loaded.

### 5. Verify locally

```bash
uv sync
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pytest -v
```

Run relevant PostgreSQL integration tests whenever SQL, catalog queries, transactions, extensions, pooling, or version-dependent behavior changes. A skipped required integration test is not a pass.

### 6. Self-review the diff

Check the complete diff, not only the files you intended to change. Remove dead code, stale comments, temporary diagnostics, duplicate constants, unused imports, and orphan files. Confirm documentation and error messages match actual behavior.

### 7. Open a pull request

The pull request must explain:

- what changed and why;
- user and developer impact;
- security and compatibility considerations;
- tests and manual verification;
- known limitations and follow-up work.

Draft pull requests are appropriate while CI or design work remains. Mark a pull request ready only when the diff is complete and the description reflects it.

### 8. Merge only green, reviewed work

Required lint, type, unit, integration, and coverage checks must pass. Re-read the final diff after automated fixes. Squash merge focused work so `main` receives one intentional commit with a durable message.

### 9. Release deliberately

Update user-facing documentation and the changelog for behavior or compatibility changes. Use semantic versioning. Verify the built wheel and container rather than assuming source-tree tests cover packaging.

## Database safety rules

- Restricted mode must remain the default.
- User SQL must be single-statement, parameterized, timed, and bounded.
- Raw values must not be interpolated into SQL.
- Mutations require explicit row guards.
- Multi-step mutations must share one connection and transaction.
- Any failure before a confirmed commit must result in rollback.
- Non-transactional PostgreSQL operations need dedicated workflows.
- Tests must cover denial paths, timeout, cancellation, rollback, and cleanup.

## Code style

- Python 3.12 or newer.
- Type annotations for public and non-trivial internal APIs.
- Google-style docstrings for public APIs.
- Ruff formatting and linting with the repository configuration.
- Pyright standard mode with repository-specific strict checks.
- Single sources of truth for constants, types, configuration, and recurring error semantics.

## Commit and pull-request style

Use concise imperative commit subjects, for example:

```text
Harden bounded SQL execution
```

A squash-merge message should describe the complete user-visible change, not an intermediate implementation step.
