# Replication and failover diagnostics

`pgsql-mcp-ha` is a focused read-only server.

## Topology

The server can inspect:

- primary or standby role
- physical replication senders
- WAL receiver state
- physical and logical slots
- logical subscriptions
- publications
- replay and write lag

## Readiness findings

The assessment can report:

- missing synchronous standbys
- stopped replay
- stopped receivers
- inactive or lost slots
- conflicting slots
- excessive retained WAL
- inactive subscriptions
- incomplete visibility

## Secret safety

Queries do not select subscription connection strings.

Queries do not select WAL receiver `conninfo`.

## Profile limits

The HA server exposes three tools.

It keeps zero warm connections and uses at most two pool connections.
