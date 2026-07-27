# Lite server

`pgsql-mcp-lite` is a small read-only server.

## Contract

- It exposes six tools.
- It has no write-mode switch.
- It keeps zero warm connections.
- It uses at most two pool connections.
- It returns at most 500 rows.
- It does not import migration, maintenance, health, provider, extension, or LLM modules.

## Start the server

```bash
DATABASE_URI='postgresql://readonly_user:password@localhost:5432/app' \
uvx pgsql-mcp-lite
```

Use a read-only PostgreSQL role.

## Intended use

Use the lite server for schema inspection, bounded reads, and non-executing query plans.

Use the full server when you need reviewed changes or advanced diagnostics.
