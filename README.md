# pgsql-mcp

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![PyPI - Version](https://img.shields.io/pypi/v/pgsql-mcp)](https://pypi.org/project/pgsql-mcp/)

A PostgreSQL Model Context Protocol server for schema inspection, bounded SQL execution, query plans, index tuning, workload analysis, and database health diagnostics.

## Safety first

`pgsql-mcp` starts in **restricted mode by default**. Restricted mode accepts one validated statement per query request, executes it in a database-enforced read-only transaction, applies a statement timeout, uses native bind parameters, and returns a bounded result.

Write access is never inferred from the database credential. It must be enabled explicitly with `--access-mode=unrestricted`, and write statements are exposed only through the guarded `execute_transaction` tool. All steps run on one connection and one transaction, and any validation, execution, row-count, timeout, cancellation, or commit failure rolls back the complete operation.

Database credentials remain the final security boundary. Use a dedicated least-privilege role, keep network transports on loopback or behind an authenticated proxy, and read [the security model](docs/security.md) before production use.

## Features

- **Schema intelligence** — inspect schemas, tables, views, sequences, columns, constraints, indexes, comments, and extensions.
- **Bounded SQL** — single-statement execution, native parameters, hard row limits, explicit truncation metadata, and precision-safe JSON encoding.
- **Guarded transactions** — atomic multi-step transactions with isolation controls, timeouts, required mutation ceilings, optional exact row-count checks, and rollback-on-failure guarantees.
- **Query plans** — inspect `EXPLAIN` plans and simulate hypothetical indexes with HypoPG.
- **Index tuning** — analyze individual queries or a `pg_stat_statements` workload.
- **Database health** — inspect index, connection, vacuum, sequence, replication, buffer, and constraint health.
- **Slow-query analysis** — rank workload queries by time or blended resource use.

## Lite profile

`pgsql-mcp-lite` is the read-only, low-context entry point for clients that only need schema inspection, bounded queries, and non-executing plans:

```bash
DATABASE_URI='postgresql://readonly_user:password@localhost:5432/app' \
  uvx pgsql-mcp-lite
```

The lite profile exposes six tools, has no write-mode switch, caps results at 500 rows, keeps zero warm connections, and limits the pool to two connections. It does not import or advertise migrations, health suites, workload analysis, index advisors, extension management, or LLM features. See [docs/lite.md](docs/lite.md) for the exact contract and MCP configuration.

LLM-backed index analysis is an optional install and is not part of the core or lite dependency set:

```bash
uvx --from 'pgsql-mcp[llm]' pgsql-mcp
```

## Quick start

### Claude Code and cloud IDEs

```json
{
  "mcpServers": {
    "postgres": {
      "command": "uvx",
      "args": ["pgsql-mcp"],
      "env": {
        "DATABASE_URI": "postgresql://readonly_user:password@localhost:5432/app"
      }
    }
  }
}
```

This configuration is read-only because restricted mode is the default.

### Explicit write-enabled development configuration

```json
{
  "mcpServers": {
    "postgres-dev": {
      "command": "uvx",
      "args": ["pgsql-mcp", "--access-mode=unrestricted"],
      "env": {
        "DATABASE_URI": "postgresql://developer:password@localhost:5432/app_dev"
      }
    }
  }
}
```

Do not reuse a production owner or superuser credential.

### SSE transport

```bash
DATABASE_URI='postgresql://readonly_user:password@localhost:5432/app' \
  uvx pgsql-mcp --transport=sse
```

The SSE server binds to `localhost:8000` by default. Put it behind authentication before exposing it beyond the local machine.

### Docker

```bash
docker run -i --rm \
  -e DATABASE_URI='postgresql://readonly_user:password@host.docker.internal:5432/app' \
  pgsql-mcp
```

Write-enabled development use must be explicit:

```bash
docker run -i --rm \
  -e DATABASE_URI='postgresql://developer:password@host.docker.internal:5432/app_dev' \
  pgsql-mcp --access-mode=unrestricted
```

## Query execution contract

`execute_sql` accepts:

- `sql`: exactly one PostgreSQL statement;
- `params`: values for psycopg `%s`, `%b`, or `%t` positional placeholders;
- `max_rows`: requested row ceiling, up to the server hard limit.

Values are never interpolated into SQL by pgsql-mcp. The original SQL and parameter list are sent separately to psycopg. The safety parser receives a placeholder-normalized copy that preserves quoted strings, identifiers, comments, and dollar-quoted bodies.

A result includes the command, portable column metadata, rows, returned row count, affected row count where PostgreSQL reports one, and a `truncated` flag. Large integers, arbitrary-precision numerics, binary values, temporal values, UUIDs, ranges, and unknown extension values use tagged JSON where ordinary JSON would lose information.

## Atomic transaction contract

`execute_transaction` is registered only in unrestricted mode. Each step supports:

```json
{
  "sql": "UPDATE app.users SET status = %s WHERE id = %s AND version = %s RETURNING id, version",
  "params": ["active", 42, 7],
  "expected_rows": 1,
  "max_affected_rows": 1,
  "result_mode": "rows",
  "max_rows": 10
}
```

The transaction tool:

1. validates every step before checking out a connection;
2. permits only `SELECT`, `INSERT`, `UPDATE`, `DELETE`, and supported `MERGE` statements;
3. rejects multiple statements, transaction-control statements, locking selects, `SELECT INTO`, and data-modifying CTEs;
4. requires `WHERE` plus `max_affected_rows` for `UPDATE` and `DELETE`;
5. requires `max_affected_rows` for every mutation;
6. applies transaction-local statement, lock, idle-in-transaction, row-security, and search-path settings;
7. rolls back the entire transaction on every failure, including cancellation and commit failure;
8. reports a successful result only after `COMMIT` completes.

PostgreSQL operations such as `VACUUM`, concurrent index operations, sequence advancement, and external side effects have different transactional semantics and are intentionally outside this API.

## Access modes

| Mode | Default | Behavior |
|---|---:|---|
| `restricted` | Yes | One validated statement, database-enforced read-only transaction, timeout, bounded rows |
| `unrestricted` | No | The same read-only `execute_sql` tool plus guarded atomic transaction writes |

The `get_server_capabilities` tool reports the effective profile, access mode, limits, transaction availability, and result encoding.

## Configuration

| CLI option | Environment variable | Default |
|---|---|---|
| positional `database_url` | `DATABASE_URI` | required |
| `--access-mode` | — | `restricted` |
| `--transport` | — | `stdio` |
| `--query-timeout` | `QUERY_TIMEOUT` | `30` seconds |
| `--max-rows` | `MAX_ROWS` | `100` |
| `--sse-host` | `SSE_HOST` | `localhost` |
| `--sse-port` | `SSE_PORT` | `8000` |
| `--sse-path` | `SSE_PATH` | `/sse` |
| `--cors-allow-origins` | `CORS_ALLOW_ORIGINS` | unset |

The full server's absolute result ceiling is 5,000 rows. The lite ceiling is 500 rows. Wildcard CORS never enables credentialed cross-origin requests.

## Optional PostgreSQL extensions

Index and workload analysis can use:

```sql
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
CREATE EXTENSION IF NOT EXISTS hypopg;
```

Install extensions through a controlled migration or administrator workflow. Restricted query execution does not grant extension-management capability.

## MCP tools

| Tool | Description |
|---|---|
| `get_server_capabilities` | Report active safety policy and hard limits |
| `list_schemas` | List database schemas |
| `list_objects` | List tables, views, sequences, or extensions |
| `get_object_details` | Inspect columns, constraints, indexes, and comments |
| `execute_sql` | Execute one bounded, parameterized, read-only statement in every mode |
| `execute_transaction` | Execute guarded steps atomically; unrestricted mode only |
| `explain_query` | Inspect a validated read-only plan; `ANALYZE` is blocked in restricted mode |
| `get_top_queries` | Analyze `pg_stat_statements` workload data |
| `analyze_workload_indexes` | Recommend indexes for a workload |
| `analyze_query_indexes` | Recommend indexes for supplied queries |
| `analyze_db_health` | Run database health diagnostics |

## Development

```bash
uv sync
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pytest -v
```

Changes follow the single-maintainer lifecycle in [CONTRIBUTING.md](CONTRIBUTING.md). The execution architecture and invariants are documented in [docs/architecture/execution-safety.md](docs/architecture/execution-safety.md).

## License

MIT
