# PostgreSQL compatibility

The supported major versions are PostgreSQL 14 through PostgreSQL 18.

## Blocking matrix

The compatibility workflow runs independent jobs for each supported major.

Each job verifies the selected server version before it runs tests.

A Docker build, startup, or readiness failure fails the job.

## Covered domains

The matrix covers:

- catalog and type inspection
- typed data operations
- reviewed migrations
- reviewed maintenance
- replication diagnostics
- provider profiles
- extension object inventory

## Fast pull request pair

The ordinary test suite uses PostgreSQL 15 and PostgreSQL 16.

The five-major matrix remains a separate blocking workflow.

## Local run

```bash
PGSQL_MCP_TEST_POSTGRES_IMAGE=postgres:18 \
uv run pytest -v tests/integration
```

Docker must be available.
