# PostgreSQL replication and HA diagnostics

`pgsql-mcp-ha` is a separate, read-only MCP profile for incident response, replication review, and failover preparation. It does not expose raw SQL, writes, migrations, maintenance, index advice, or LLM features.

## Tool surface

| Tool | Purpose |
|---|---|
| `get_server_capabilities` | Report the profile, hard limits, and secret-redaction contract |
| `get_replication_topology` | Capture one bounded physical and logical replication snapshot |
| `assess_failover_readiness` | Capture topology and evaluate deterministic operator thresholds |

Each catalog is capped at 100 rows. The implementation uses one `REPEATABLE READ READ ONLY` transaction, a statement timeout, row security, a `pg_catalog` search path, and explicit rollback before returning the connection to the pool.

## Data captured

The topology reports:

- primary or standby role and PostgreSQL major;
- WAL level, sender/slot capacity, hot-standby state, and whether synchronous standby policy is configured;
- physical sender state, sync state, LSNs, byte lag, and time lag;
- physical and logical slots, active state, retained WAL, WAL status, safe WAL size, and conflict state where the server exposes it;
- standby WAL receiver status and endpoint metadata;
- logical subscription worker state and progress;
- publication behavior;
- catalogs that the monitoring role could not inspect.

The adapter deliberately never selects:

- `pg_subscription.subconninfo`;
- `pg_stat_wal_receiver.conninfo`;
- the configured database URL;
- SQL values or database exception details.

## Readiness findings

The assessment is deterministic and local to the captured snapshot. It flags:

- missing or non-streaming WAL receiver on a standby;
- paused replay;
- byte or time lag above caller-supplied warning and critical thresholds;
- synchronous policy with no visible standby sender;
- senders outside streaming state;
- inactive slots retaining excessive WAL;
- slots that lost WAL or conflict on a standby;
- inactive logical-subscription workers;
- incomplete visibility caused by role privileges or provider restrictions.

A result is `ready` only when there are no warning or critical findings. The profile does not promote nodes, change replication settings, drop slots, or claim that a snapshot alone proves an application-level failover is safe.

## Monitoring role

Start with ordinary `CONNECT` access. Depending on PostgreSQL version and provider policy, complete visibility can require membership in `pg_monitor` or narrower catalog privileges. The server records unavailable catalog classes instead of escalating credentials or exposing connection secrets.

## Local validation

With Docker available:

```bash
PGSQL_MCP_TEST_POSTGRES_IMAGE=postgres:18 uv run pytest -v \
  tests/unit/replication \
  tests/unit/ha \
  tests/integration/replication
```

The blocking compatibility matrix runs the real-database replication contracts against PostgreSQL 14, 15, 16, 17, and 18.
