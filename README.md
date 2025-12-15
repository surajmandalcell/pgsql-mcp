# Postgres MCP

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![PyPI - Version](https://img.shields.io/pypi/v/postgres-mcp)](https://pypi.org/project/postgres-mcp/)

A PostgreSQL MCP server with index tuning, explain plans, health checks, and safe SQL execution.

## Features

- **Database Health** - analyze index health, connection utilization, buffer cache, vacuum health, and more
- **Index Tuning** - find optimal indexes for your workload using industrial-strength algorithms
- **Query Plans** - review EXPLAIN plans and simulate hypothetical indexes
- **Schema Intelligence** - context-aware SQL generation
- **Safe SQL Execution** - configurable read-only mode for production use

## Quick Start

### Claude Code / Cloud IDEs

For Claude Code or cloud-based IDEs, add to your MCP configuration:

```json
{
  "mcpServers": {
    "postgres": {
      "command": "uvx",
      "args": ["postgres-mcp", "--access-mode=unrestricted"],
      "env": {
        "DATABASE_URI": "postgresql://username:password@localhost:5432/dbname"
      }
    }
  }
}
```

### VS Code / Cursor / Windsurf

**Using SSE (recommended for IDEs):**

1. Start the server:
```bash
docker run -p 8000:8000 \
  -e DATABASE_URI=postgresql://username:password@localhost:5432/dbname \
  crystaldba/postgres-mcp --access-mode=unrestricted --transport=sse
```

2. Add to your MCP config (`mcp.json` for Cursor, `mcp_config.json` for Windsurf):

```json
{
  "mcpServers": {
    "postgres": {
      "type": "sse",
      "url": "http://localhost:8000/sse"
    }
  }
}
```

> Note: Windsurf uses `serverUrl` instead of `url`.

**Using stdio:**

```json
{
  "mcpServers": {
    "postgres": {
      "command": "docker",
      "args": [
        "run", "-i", "--rm",
        "-e", "DATABASE_URI",
        "crystaldba/postgres-mcp",
        "--access-mode=unrestricted"
      ],
      "env": {
        "DATABASE_URI": "postgresql://username:password@localhost:5432/dbname"
      }
    }
  }
}
```

### Docker MCP Platform

```bash
docker pull crystaldba/postgres-mcp
```

Run with stdio:
```bash
docker run -i --rm \
  -e DATABASE_URI=postgresql://username:password@localhost:5432/dbname \
  crystaldba/postgres-mcp --access-mode=unrestricted
```

Run with SSE:
```bash
docker run -p 8000:8000 \
  -e DATABASE_URI=postgresql://username:password@localhost:5432/dbname \
  crystaldba/postgres-mcp --access-mode=unrestricted --transport=sse
```

### Python Installation

```bash
pipx install postgres-mcp
# or
uv pip install postgres-mcp
```

## Access Modes

- **`--access-mode=unrestricted`** - Full read/write access (development)
- **`--access-mode=restricted`** - Read-only with resource limits (production)

## Optional: Postgres Extensions

For full index tuning capabilities, install these extensions:

```sql
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
CREATE EXTENSION IF NOT EXISTS hypopg;
```

## Available Tools

| Tool | Description |
|------|-------------|
| `list_schemas` | List all database schemas |
| `list_objects` | List tables, views, sequences in a schema |
| `get_object_details` | Get columns, constraints, indexes for an object |
| `execute_sql` | Execute SQL (read-only in restricted mode) |
| `explain_query` | Get query execution plans with hypothetical index support |
| `get_top_queries` | Find slowest queries via pg_stat_statements |
| `analyze_workload_indexes` | Recommend indexes for your workload |
| `analyze_query_indexes` | Recommend indexes for specific queries |
| `analyze_db_health` | Run comprehensive health checks |

## License

MIT
