# Concurrency and cancellation release gates

The dedicated `Concurrency stress` workflow exercises real PostgreSQL behavior that ordinary unit coverage cannot prove. It is intentionally separate from the fast suite and runs against PostgreSQL 16 with `PGSQL_MCP_RUN_STRESS=1`.

## Blocking contracts

- A one-connection pool must reject a second concurrent lease within a bounded timeout and recover immediately after the held lease is returned.
- One hundred concurrent parameterized bounded reads must complete through a five-connection pool without incorrect results or an idle-in-transaction leak.
- Cancelling an in-flight bounded read must propagate `CancelledError`, close any server-side cursor, return the connection to the idle transaction state, and leave the same connection reusable.
- Stress evidence is uploaded as text and JUnit XML for every run.

## Local reproduction

With Docker available:

```bash
PGSQL_MCP_RUN_STRESS=1 \
PGSQL_MCP_TEST_POSTGRES_IMAGE=postgres:16 \
uv run pytest -q tests/integration/stress
```

These tests are skipped in ordinary and compatibility runs unless the explicit stress flag is set. This keeps the normal development loop fast while preserving a blocking release contract for pool exhaustion, fan-out, cancellation races, and transaction cleanup.
