# pgsql-mcp

<p align="center">
  <strong>A controlled PostgreSQL operations layer for Model Context Protocol clients.</strong>
</p>

<p align="center">
  <a href="https://github.com/surajmandalcell/pgsql-mcp/actions/workflows/build.yml"><img alt="Main CI" src="https://github.com/surajmandalcell/pgsql-mcp/actions/workflows/build.yml/badge.svg?branch=main"></a>
  <a href="https://github.com/surajmandalcell/pgsql-mcp/actions/workflows/postgres-compatibility.yml"><img alt="PostgreSQL compatibility" src="https://github.com/surajmandalcell/pgsql-mcp/actions/workflows/postgres-compatibility.yml/badge.svg?branch=main"></a>
  <a href="https://github.com/surajmandalcell/pgsql-mcp/actions/workflows/ste-docs.yml"><img alt="ASD-STE100 profile" src="https://github.com/surajmandalcell/pgsql-mcp/actions/workflows/ste-docs.yml/badge.svg?branch=main"></a>
  <img alt="Python 3.12" src="https://img.shields.io/badge/Python-3.12-0f62fe?style=flat-square">
  <img alt="PostgreSQL 14 through 18" src="https://img.shields.io/badge/PostgreSQL-14--18-0f62fe?style=flat-square">
  <img alt="MIT license" src="https://img.shields.io/badge/License-MIT-161616?style=flat-square">
</p>

`pgsql-mcp` gives MCP clients safe PostgreSQL inspection, bounded SQL, reviewed changes, and operational diagnostics.

The server starts in restricted mode. Write tools require an explicit unrestricted configuration.

<p align="center">
  <img src="docs/assets/pgsql-mcp-architecture.svg" alt="pgsql-mcp system architecture" width="100%">
</p>

## Why use pgsql-mcp

| Control | Behavior | Evidence |
|---|---|---|
| Safe default | Restricted mode permits bounded read operations | Database read-only transactions and SQL validation |
| Reviewed change | Migration and maintenance plans require a review hash | Durable ledgers and commit guards |
| Bounded output | Public tools apply row, time, and result limits | Server-side cursors and hard ceilings |
| Broad catalog | Core catalogs expose relations, types, extensions, and providers | OID-backed identities and deterministic results |
| Release quality | Main commits run the complete quality system | Tests, coverage, mutation, stress, and compatibility jobs |

## Safety boundary

Use a dedicated PostgreSQL role with the minimum required privileges.

Use `stdio` for local clients when possible. Put remote transports behind authentication and transport encryption.

<p align="center">
  <img src="docs/assets/pgsql-mcp-safety-flow.svg" alt="pgsql-mcp request safety flow" width="100%">
</p>

Read [the security model](docs/security.md) before production use.

## Quick start

Set a read-only database URI.

```bash
DATABASE_URI='postgresql://readonly_user:password@localhost:5432/app' \
uvx pgsql-mcp
```

Use unrestricted mode only for a controlled environment.

```bash
DATABASE_URI='postgresql://developer:password@localhost:5432/app_dev' \
uvx pgsql-mcp --access-mode=unrestricted
```

Do not use a production owner or superuser role.

### MCP configuration

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

### Docker

```bash
docker run -i --rm \
  -e DATABASE_URI='postgresql://readonly_user:password@host.docker.internal:5432/app' \
  pgsql-mcp
```

## Server profiles

| Command | Scope | Pool | Write access |
|---|---|---:|---|
| `pgsql-mcp` | Full catalog, SQL, diagnostics, and reviewed changes | Configured pool | Explicit unrestricted mode |
| `pgsql-mcp-lite` | Six focused read-only tools | Two connections | No |
| `pgsql-mcp-ha` | Replication and failover inspection | Focused pool | No |

## Capability map

### Catalog and diagnostics

- Inspect schemas, relations, routines, types, privileges, policies, and partitions.
- Search trusted PostgreSQL catalogs.
- Report installed and available extension profiles.
- Inventory objects that belong to an installed extension.
- Report PostGIS columns and spatial indexes.
- Report pgvector columns and indexes.
- Report provider capabilities without reading secrets.
- Inspect replication topology and failover readiness.

### SQL and data

- Execute one bounded read-only SQL statement.
- Bind values with native PostgreSQL parameters.
- Select typed pages with stable keyset pagination.
- Insert, upsert, update, and delete rows with commit guards.
- Execute guarded atomic transactions in unrestricted mode.

### Reviewed operations

- Create and hash transactional migration plans.
- Apply and roll back reviewed migrations.
- Create and hash nontransactional maintenance plans.
- Apply maintenance and reconcile an unknown outcome.

### Analysis

- Explain validated queries.
- Read bounded workload statistics.
- Analyze query and workload index opportunities.
- Run database health checks.
- Publish privacy-preserving runtime metrics.

## Hard limits

| Surface | Limit |
|---|---:|
| Full server rows | 5,000 |
| Lite server rows | 500 |
| Typed data rows | 500 |
| Extension objects | 500 |
| PostGIS columns and indexes | 500 combined |
| pgvector columns and indexes | 500 combined |

Protected operations also apply query, lock, and idle-transaction timeouts.

## Production use

The current release scope is ready for trusted internal and operator workflows when all checklist items are true.

- Use a dedicated database role.
- Keep restricted mode unless a reviewed write workflow is required.
- Keep remote transports behind authentication and TLS.
- Set explicit row and timeout limits.
- Monitor the main branch quality jobs.
- Test each upgrade against a disposable PostgreSQL database.
- Review [production readiness](docs/production-readiness.md) before deployment.

Future roadmap items are not release guarantees. Read [the project plan](plan.md) for planned work.

## Documentation

| Topic | Guide |
|---|---|
| Security and deployment | [Security model](docs/security.md) |
| Production review | [Production readiness](docs/production-readiness.md) |
| Architecture | [Execution safety](docs/architecture/execution-safety.md) |
| Compatibility | [PostgreSQL compatibility](docs/compatibility.md) |
| Catalog model | [Catalog inspection](docs/catalog.md) |
| Typed data | [Data operations](docs/data-operations.md) |
| Reviewed changes | [Migrations](docs/migrations.md) and [maintenance](docs/maintenance.md) |
| Extensions | [Profiles](docs/extensions.md), [objects](docs/extension-objects.md), [PostGIS](docs/postgis.md), and [pgvector](docs/pgvector.md) |
| High availability | [Replication](docs/replication.md) |
| Quality system | [Testing and documentation index](docs/testing.md) |
| Writing profile | [ASD-STE100 project profile](docs/asd-ste100.md) |

## Development

```bash
uv sync --all-extras
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pytest -v
python scripts/check_ste_docs.py .
```

Run the complete gates locally before merge. GitHub Actions run only after a commit reaches `main`.

Read [CONTRIBUTING.md](CONTRIBUTING.md) before you change the project.

## Release

Main branch jobs build and validate the package and container. They do not publish artifacts.

Read [PUBLISHING.md](PUBLISHING.md) for the manual release procedure.

## License

MIT
