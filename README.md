# pgsql-mcp

`pgsql-mcp` is a PostgreSQL Model Context Protocol server.

It provides safe SQL access, catalog inspection, reviewed changes, diagnostics, and release-quality checks.

## Safety defaults

The full server starts in restricted mode.

Restricted mode permits one validated read-only statement.

The server applies database-enforced read-only transactions, timeouts, row limits, and native parameters.

Write tools are available only when you set `--access-mode=unrestricted`.

Use a dedicated PostgreSQL role with the minimum required privileges.

Read [the security model](docs/security.md) before production use.

## Server profiles

| Command | Purpose |
|---|---|
| `pgsql-mcp` | Full server with restricted and unrestricted modes |
| `pgsql-mcp-lite` | Six-tool read-only server with a two-connection pool |
| `pgsql-mcp-ha` | Three-tool read-only replication and failover server |

## Main capabilities

- Inspect schemas, relations, routines, types, privileges, policies, and partitions.
- Execute bounded read-only SQL with native parameters.
- Execute guarded atomic transactions in unrestricted mode.
- Plan, apply, inspect, and roll back reviewed transactional migrations.
- Plan, apply, inspect, and reconcile reviewed maintenance operations.
- Select and change rows through typed structured requests.
- Inspect replication topology and failover readiness.
- Report provider capabilities from explicit hints or strong catalog markers.
- List installed and available extension profiles.
- Inventory objects that belong to an installed extension.
- Report PostGIS columns and spatial indexes from PostgreSQL core catalogs.
- Explain queries and analyze index opportunities.
- Publish privacy-preserving runtime metrics.

## Quick start

Set a read-only database URI.

```bash
DATABASE_URI='postgresql://readonly_user:password@localhost:5432/app' \
uvx pgsql-mcp
```

Use unrestricted mode only for a controlled development database.

```bash
DATABASE_URI='postgresql://developer:password@localhost:5432/app_dev' \
uvx pgsql-mcp --access-mode=unrestricted
```

Do not use a production owner or superuser role.

## MCP configuration

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

Restricted mode is the default for this configuration.

## Docker

```bash
docker run -i --rm \
  -e DATABASE_URI='postgresql://readonly_user:password@host.docker.internal:5432/app' \
  pgsql-mcp
```

## Important limits

- The full server returns at most 5,000 rows.
- The lite server returns at most 500 rows.
- Typed data operations return at most 500 rows.
- Extension object inventories return at most 500 objects.
- PostGIS diagnostics return at most 500 combined columns and indexes.
- Server-side cursors fetch only the visible row ceiling plus one row.
- Query, lock, and idle-transaction timeouts apply inside protected operations.

## Tools

### Core and catalog

| Tool | Purpose |
|---|---|
| `get_server_capabilities` | Report the active profile and hard limits |
| `list_schemas` | List database schemas |
| `list_objects` | List common objects in one schema |
| `get_object_details` | Inspect one common object |
| `get_server_info` | Report bounded server metadata |
| `search_catalog` | Search trusted PostgreSQL catalogs |
| `list_relations` | List all supported relation classes |
| `get_relation_details` | Inspect one relation by live catalog identity |
| `list_postgres_types` | List PostgreSQL types by OID |
| `get_postgres_type` | Inspect one PostgreSQL type |
| `get_extension_profiles` | List extension capability profiles |
| `get_extension_objects` | List objects that belong to one extension |
| `get_postgis_diagnostics` | Report bounded PostGIS columns and spatial indexes |
| `get_deployment_profile` | Report provider capabilities without secrets |

### SQL and data

| Tool | Purpose |
|---|---|
| `execute_sql` | Execute one bounded read-only statement |
| `execute_transaction` | Execute guarded atomic steps |
| `select_rows` | Select a typed bounded page |
| `insert_rows` | Insert a typed batch |
| `upsert_rows` | Upsert through a verified unique key |
| `update_rows` | Update rows with commit guards |
| `delete_rows` | Delete rows with commit guards |

### Reviewed changes

| Tool | Purpose |
|---|---|
| `create_migration_plan` | Create and hash a transactional migration plan |
| `apply_migration_plan` | Apply a reviewed migration |
| `get_migration_status` | Read migration ledger metadata |
| `rollback_migration` | Roll back the latest reviewed migration |
| `create_maintenance_plan` | Create and hash a maintenance plan |
| `apply_maintenance_plan` | Apply reviewed nontransactional maintenance |
| `get_maintenance_status` | Read maintenance ledger metadata |
| `reconcile_maintenance_operation` | Resolve an unknown maintenance outcome |

### Analysis

| Tool | Purpose |
|---|---|
| `explain_query` | Inspect a validated query plan |
| `get_top_queries` | Read bounded workload statistics |
| `analyze_workload_indexes` | Analyze workload index opportunities |
| `analyze_query_indexes` | Analyze supplied query index opportunities |
| `analyze_db_health` | Run database health checks |

The HA profile also provides replication topology and failover-readiness tools.

## Configuration

| CLI option | Environment variable | Default |
|---|---|---|
| positional `database_url` | `DATABASE_URI` | required |
| `--access-mode` | none | `restricted` |
| `--transport` | none | `stdio` |
| `--query-timeout` | `QUERY_TIMEOUT` | `30` seconds |
| `--max-rows` | `MAX_ROWS` | `100` |
| `--migration-schema` | `MIGRATION_SCHEMA` | `public` |
| `--maintenance-schema` | `MAINTENANCE_SCHEMA` | `public` |
| `--sse-host` | `SSE_HOST` | `localhost` |
| `--sse-port` | `SSE_PORT` | `8000` |
| `--sse-path` | `SSE_PATH` | `/sse` |

## Development

```bash
uv sync --all-extras
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pytest -v
python scripts/check_ste_docs.py .
```

Read [CONTRIBUTING.md](CONTRIBUTING.md) before you change the project.

The documentation index is in [docs/testing.md](docs/testing.md).

## License

MIT
