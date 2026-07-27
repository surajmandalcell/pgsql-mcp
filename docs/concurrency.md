# Concurrency and cancellation release gates

The dedicated stress workflow tests real PostgreSQL behavior.

## Release requirements

- A one-connection pool must time out a second lease.
- The pool must recover after the first lease returns.
- One hundred bounded reads must complete through five connections.
- Each read must return the correct bound value.
- The test must not leave an idle transaction.
- A cancelled read must raise `CancelledError`.
- A cancelled read must close its server-side cursor.
- The connection must return to the idle transaction state.
- The same connection must remain reusable.

## Local run

```bash
PGSQL_MCP_RUN_STRESS=1 \
PGSQL_MCP_TEST_POSTGRES_IMAGE=postgres:16 \
uv run pytest -q tests/integration/stress
```

The ordinary suite skips these tests unless the stress flag is set.
