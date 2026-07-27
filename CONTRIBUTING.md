# Contributing

Use this process for each change.

## Prepare the environment

1. Install `uv`.
2. Run `uv sync --all-extras`.
3. Create a branch from the current `main` branch.
4. Keep the branch focused on one bounded context.

## Write tests first

1. Add a test that shows the required behavior.
2. Run the focused test and confirm that it fails.
3. Add the smallest correct implementation.
4. Run the focused test again.
5. Run the complete quality gates.

Do not weaken an existing safety test to make new code pass.

## Required gates

```bash
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pytest -v
python scripts/check_ste_docs.py .
```

Changes to safety-critical code also require changed-line coverage and mutation checks.

Database behavior requires real PostgreSQL integration tests.

Compatibility-sensitive behavior requires PostgreSQL 14 through PostgreSQL 18.

## Commit rules

- Use clear imperative commit subjects.
- Keep generated transfer files out of final pull requests.
- Do not commit credentials, database URLs, or test secrets.
- Keep documentation consistent with the final code.
- Use ASD-STE100 style for Markdown prose.

## Pull request rules

1. Describe the bounded context.
2. Describe the test-first sequence.
3. List safety invariants.
4. List validation results.
5. Resolve all review threads.
6. Merge only a source-only green head.

Use squash merge unless the change needs preserved commit structure.
