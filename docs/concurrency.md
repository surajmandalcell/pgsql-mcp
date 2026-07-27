# Concurrency and cancellation release gates

The `Concurrency stress` workflow tests real PostgreSQL behavior. The standard unit tests cannot prove this behavior.

The workflow uses PostgreSQL 16. It sets `PGSQL_MCP_RUN_STRESS=1` before it starts the tests.

## Release requirements

- A one-connection pool must reject a second lease before the timeout expires.
- The pool must recover after the first lease returns.
- One hundred bounded reads must complete through a five-connection pool.
- Each bounded read must return the correct bound value.
- The test must not leave an idle transaction.
- A cancelled read must raise `CancelledError`.
- A cancelled read must close its server-side cursor.
- The connection must return to the idle transaction state.
- The same connection must be reusable after cancellation.
- Each run must upload text output and JUnit XML.

## Run the tests locally

1. Start Docker.
2. Run this command:

```bash
PGSQL_MCP_RUN_STRESS=1 \
PGSQL_MCP_TEST_POSTGRES_IMAGE=postgres:16 \
uv run pytest -q tests/integration/stress
```

The standard test workflows skip these tests. The dedicated workflow keeps them as blocking release requirements.
