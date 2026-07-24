# pgsql-mcp-lite

`pgsql-mcp-lite` is the small, deterministic server profile for editor assistants, local automation, and environments where reliability and MCP context size matter more than broad database administration.

It is a separate console command in the same release:

```bash
DATABASE_URI='postgresql://readonly_user:password@localhost:5432/app' \
  uvx pgsql-mcp-lite
```

## Tool surface

The lite server exposes exactly six tools:

| Tool | Purpose |
|---|---|
| `get_server_capabilities` | Report the immutable lite policy and hard limits |
| `list_schemas` | List PostgreSQL schemas |
| `list_objects` | List tables, views, sequences, or extensions |
| `get_object_details` | Inspect columns, constraints, indexes, and comments |
| `execute_sql` | Execute one parameterized, bounded, read-only statement |
| `explain_query` | Produce non-executing `EXPLAIN` output |

It deliberately omits writes, transactions, migrations, health suites, workload analysis, index advisors, extension management, and LLM-backed features. This keeps tool descriptions short and removes failure modes caused by optional extensions or administrative privileges.

The base distribution does not require the LLM client stack. Install `pgsql-mcp[llm]` only when the full server must use the `llm` index-advisor method.

## Reliability limits

- Read-only behavior cannot be disabled from the lite CLI.
- Results are capped at 500 rows, even when a caller requests more.
- The default pool has no warm connections and allows at most two concurrent database connections.
- SQL parsing, AST validation, and execution share one client-side timeout.
- PostgreSQL also enforces read-only transactions and server-side timeouts.
- `EXPLAIN ANALYZE` and hypothetical indexes are not available.
- Values use the same precision-preserving tagged JSON fallback as the full server.

## MCP configuration

```json
{
  "mcpServers": {
    "postgres-lite": {
      "command": "uvx",
      "args": ["pgsql-mcp-lite"],
      "env": {
        "DATABASE_URI": "postgresql://readonly_user:password@localhost:5432/app"
      }
    }
  }
}
```

## Choosing a profile

Use `pgsql-mcp-lite` when the assistant needs schema context, bounded reads, and plans. Use `pgsql-mcp` when health analysis, workload inspection, index recommendations, or guarded write transactions are required.

Both profiles should use a dedicated least-privilege database role. The profile limits complement database permissions; they do not replace them.
