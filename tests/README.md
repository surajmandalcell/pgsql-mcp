# Test suite

The test suite contains unit, integration, mutation, compatibility, performance, and stress contracts.

## Run common tests

```bash
uv run pytest -v
```

## Run focused tests

```bash
uv run pytest -q tests/unit/sql
uv run pytest -q tests/integration/catalog
```

## Database tests

Docker-backed tests create disposable PostgreSQL containers.

Set `PGSQL_MCP_TEST_POSTGRES_IMAGE` to select a supported major version.

## Stress tests

```bash
PGSQL_MCP_RUN_STRESS=1 \
PGSQL_MCP_TEST_POSTGRES_IMAGE=postgres:16 \
uv run pytest -q tests/integration/stress
```

Read [TEST_PLAN.md](TEST_PLAN.md) for the coverage map.
