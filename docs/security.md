# Security model

This document defines the security boundary of pgsql-mcp. It is part of the public API contract: changes that weaken an invariant require an explicit security review and release note.

## Trust boundaries

pgsql-mcp sits between an MCP client and PostgreSQL. It does not make an over-privileged database credential safe. PostgreSQL authentication, privileges, ownership, row-level security, network policy, TLS, and provider controls remain authoritative.

Use separate roles for inspection, application reads, guarded writes, and administration. A production MCP role should not be a superuser, database owner, table owner, replication role, or a role with `BYPASSRLS`. Grant access only to required databases, schemas, relations, columns, and functions.

## Restricted mode

Restricted mode is the default. Public SQL execution has the following layers:

1. exactly one statement is accepted;
2. psycopg placeholders are counted without rewriting strings, comments, identifiers, or dollar-quoted bodies;
3. PostgreSQL syntax is parsed with pglast;
4. the existing deep AST allowlist rejects disallowed statements, functions, locks, and unsafe shapes;
5. the original SQL and bind values are sent separately to psycopg;
6. PostgreSQL executes the statement in `BEGIN TRANSACTION READ ONLY`;
7. transaction-local statement, lock, and idle-in-transaction timeouts are applied;
8. row security is enabled and the search path is reset before the query;
9. a client-side timeout covers validation and database execution;
10. results are bounded and tagged where JSON would be lossy;
11. the transaction is rolled back before the connection returns to the pool.

Read-only transactions prevent PostgreSQL data and schema changes, but they do not neutralize every possible external side effect in a procedural language or extension. Least-privilege function execution rights are therefore required even in restricted mode.

## Unrestricted mode

Unrestricted mode is an explicit development or controlled-automation capability. `execute_sql` remains read-only, single-statement, parameterized, and result-bounded. Enabling unrestricted mode registers `execute_transaction`; it does not create a raw SQL write escape hatch.

Use `execute_transaction` for writes. It validates every step before connection acquisition and applies all steps on one connection and one transaction. Mutations require an affected-row ceiling; `UPDATE` and `DELETE` additionally require a parsed `WHERE` clause. Optional `expected_rows` provides optimistic concurrency and invariant checking. Unknown affected-row counts cause rollback rather than bypassing a guard.

The transaction remains active until `COMMIT` succeeds. Validation errors, database errors, row-count mismatches, timeouts, cancellation, and commit failures trigger rollback. Connections are not intentionally returned to the pool with an open or aborted transaction.

## Operations outside the atomic API

Not every PostgreSQL operation has ordinary transactional behavior. Examples include:

- `VACUUM`, which cannot run inside a transaction block;
- concurrent index and reindex variants with transaction restrictions;
- sequence advancement, which is not rolled back like table writes;
- notifications, foreign data wrappers, procedural languages, and extensions that can affect external systems;
- `EXPLAIN ANALYZE`, which executes the underlying statement.

These operations require dedicated plan/apply/status workflows. They are not accepted by the generic atomic transaction API.

## Query results

The server limits returned rows and fetches only one additional row to identify truncation. Callers must check `truncated` before treating a result as complete. The compatibility driver used by internal analysis code can still consume complete internal result sets; user-supplied SQL uses the bounded path.

JSON numbers cannot represent every PostgreSQL integer or numeric exactly. Values that would lose precision are tagged and encoded as strings. Binary data uses base64. Unknown extension values retain a type tag and canonical string rather than causing a response failure.

## Network transport

stdio is the safest default because it inherits the local process boundary. SSE binds to `localhost` by default. pgsql-mcp does not yet provide a complete internet-facing identity layer; place SSE behind an authenticated, TLS-terminating reverse proxy and network allowlist before remote exposure.

Do not combine wildcard CORS with credentials. The server disables credentialed CORS when `*` is selected.

## Secrets and logs

Connection passwords are redacted from common URL and DSN forms before connection errors are logged. Do not enable SQL-value logging in production. Application logs, reverse proxies, MCP clients, shell history, process arguments, crash reports, and observability exporters must also be configured to avoid credential and parameter disclosure.

Prefer `DATABASE_URI` supplied by a secret manager over a command-line connection string, because process arguments may be visible to other users on the host.

## Reporting vulnerabilities

Do not publish an exploit in a pull request. Contact the maintainer privately with the affected version, threat model, reproduction, and suggested mitigation. A fix should include a regression test that fails without the patch.
