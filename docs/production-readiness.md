# Production readiness

The current release scope is ready for trusted internal and operator workflows when all requirements in this guide are true.

The server is not an application authorization layer.

Database credentials remain the final authorization boundary.

## Verified release scope

- Restricted mode starts by default.
- Read requests use database read-only transactions.
- Public SQL accepts one validated statement.
- Native parameters bind data values.
- Public tools apply row and timeout limits.
- Reviewed write plans require explicit unrestricted mode.
- Migration and maintenance operations use review hashes and durable ledgers.
- PostgreSQL 14 through PostgreSQL 18 run compatibility contracts.
- Main branch jobs verify tests, coverage, mutation safety, stress, documentation, and release budgets.

## Required deployment controls

- Use a dedicated PostgreSQL role.
- Grant only the required database privileges.
- Keep restricted mode for read-only deployments.
- Use separate roles for migration and maintenance work.
- Use `stdio` for local clients when possible.
- Put remote transports behind authentication and TLS.
- Keep the SSE listener on a trusted interface.
- Set explicit query and row limits.
- Send logs and metrics to a protected destination.
- Test backup and credential rotation procedures.

## Release controls

- Use a commit from `main`.
- Confirm that all main branch jobs pass.
- Build the wheel and container from the same commit.
- Test the wheel in a clean environment.
- Run a smoke test against a disposable PostgreSQL database.
- Verify package and image metadata before publication.
- Record user-visible changes in the release notes.

## Supported scope

The release supports core PostgreSQL features, PostGIS diagnostics, and pgvector diagnostics.

TimescaleDB-specific diagnostics, Citus-specific diagnostics, and resumable nontransactional migration stages remain roadmap items.

These roadmap items do not change the documented guarantees of the current release.

## Deployment decision

Approve deployment only when the database role, network boundary, limits, monitoring, and rollback procedure match this guide.
