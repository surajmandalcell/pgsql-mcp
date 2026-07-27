# Security model

Database credentials are the final authorization boundary.

Use a dedicated role with the minimum required privileges.

## Default mode

The full server starts in restricted mode.

Restricted mode exposes bounded read-only SQL and read-only inspection tools.

Write tools require explicit unrestricted mode.

## SQL safety

- Values use native PostgreSQL parameters.
- Identifiers use trusted composition.
- Public SQL accepts one statement.
- Public SQL rejects transaction control.
- Public SQL rejects data-changing common table expressions.
- Public SQL rejects session-changing functions.
- Read requests run in read-only transactions.

## Network safety

Use `stdio` for local clients when possible.

The SSE transport binds to loopback by default.

Put remote transports behind authentication and transport encryption.

Do not enable wildcard credentialed CORS.

## Secret handling

The server does not return connection strings.

Provider and replication tools do not select secret connection fields.

Logs and metrics do not store SQL text, parameter values, database identifiers, or exception messages.

## Operational guidance

Rotate credentials after suspected exposure.

Use separate roles for read, write, migration, maintenance, and monitoring duties.
