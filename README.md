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
- **Extension profiles** — inventory known and unknown installed extensions with honest catalog, type, and specialized-tool support tiers.
- **Bounded SQL** — single-statement execution, native parameters, hard row limits, explicit truncation metadata, and precision-safe JSON encoding.
- **Guarded transactions** — atomic multi-step transactions with isolation controls, timeouts, required mutation ceilings, optional exact row-count checks, and rollback-on-failure guarantees.
- **Reviewed migrations** — deterministic plan hashes, conservative DDL classification, atomic schema/ledger commits, and latest-only rollback.
- **Reviewed maintenance** — hash-bound nontransactional plans, target locks, durable status, and explicit unknown-outcome reconciliation.
- **Typed data operations** — structured filters, keyset pagination, live catalog validation, and rollback-enforced mutation ceilings.
- **Version compatibility** — core catalog, typed-data, and migration contracts exercised across PostgreSQL 14–18.
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
| `--migration-schema` | `MIGRATION_SCHEMA` | `public` |
| `--maintenance-schema` | `MAINTENANCE_SCHEMA` | `public` |
| `--sse-host` | `SSE_HOST` | `localhost` |
| `--sse-port` | `SSE_PORT` | `8000` |
| `--sse-path` | `SSE_PATH` | `/sse` |
| `--cors-allow-origins` | `CORS_ALLOW_ORIGINS` | unset |

The full server's absolute result ceiling is 5,000 rows. The lite ceiling is 500 rows. Wildcard CORS never enables credentialed cross-origin requests.

## PostgreSQL compatibility

The dedicated compatibility matrix exercises the catalog, typed-data, reviewed-migration, and reviewed-maintenance bounded contexts against PostgreSQL 14, 15, 16, 17, and 18. The ordinary pull-request suite retains PostgreSQL 15 and 16 as its fast pair. See [docs/compatibility.md](docs/compatibility.md) for the support contract, local reproduction, deployment tiers, and extension tiers.

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
| `get_server_info` | Report PostgreSQL version, role, recovery, locale, and extensions |
| `get_extension_profiles` | List bounded installed or available extension capability profiles |
| `search_catalog` | Search relations, routines, types, collations, and extensions |
| `list_relations` | List every PostgreSQL relation class with storage and RLS metadata |
| `get_relation_details` | Inspect columns, OIDs, constraints, indexes, triggers, policies, partitions, and grants |
| `list_postgres_types` | List built-in, user-defined, and extension-owned types by OID |
| `get_postgres_type` | Resolve enum, domain, composite, range, multirange, array, and unknown types |
| `execute_sql` | Execute one bounded, parameterized, read-only statement in every mode |
| `execute_transaction` | Execute guarded steps atomically; unrestricted mode only |
| `select_rows` | Read a bounded, byte-limited page through structured filters and stable keyset pagination |
| `insert_rows` | Insert a bounded typed batch with affected-row commit guards |
| `upsert_rows` | Upsert through a verified primary or non-partial unique key |
| `update_rows` | Update filtered rows with optimistic predicates and commit guards |
| `delete_rows` | Delete filtered rows with optimistic predicates and commit guards |
| `create_migration_plan` | Parse, classify, and hash ordered forward/rollback DDL without a database connection |
| `apply_migration_plan` | Verify and atomically commit a reviewed transactional plan and ledger row |
| `get_migration_status` | Return redacted trusted-ledger metadata |
| `rollback_migration` | Roll back the latest reviewed migration atomically in reverse order |
| `create_maintenance_plan` | Inspect and hash a structured nontransactional maintenance request |
| `apply_maintenance_plan` | Apply an exact reviewed maintenance plan; unrestricted mode only |
| `get_maintenance_status` | Return redacted durable maintenance state |
| `reconcile_maintenance_operation` | Resolve an unknown maintenance outcome after external verification |
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

Changes follow the single-maintainer lifecycle in [CONTRIBUTING.md](CONTRIBUTING.md). The execution architecture and invariants are documented in [docs/architecture/execution-safety.md](docs/architecture/execution-safety.md), the live OID-backed object model in [docs/catalog.md](docs/catalog.md), reviewed migrations in [docs/migrations.md](docs/migrations.md), structured CRUD in [docs/data-operations.md](docs/data-operations.md), reviewed maintenance in [docs/maintenance.md](docs/maintenance.md), extension profiles in [docs/extensions.md](docs/extensions.md), and the version support contract in [docs/compatibility.md](docs/compatibility.md).

## License

MIT
