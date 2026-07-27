# Provider capability profiles

`get_deployment_profile` reports conservative deployment capabilities.

## Identification

The caller can supply an explicit provider hint.

Automatic detection uses only strong version, setting-prefix, or role markers.

A deployment without a strong marker remains unknown.

Conflicting markers remain visible.

## Supported profiles

- upstream PostgreSQL
- generic managed PostgreSQL
- Amazon RDS
- Amazon Aurora PostgreSQL
- Google Cloud SQL
- Google AlloyDB
- Azure Database for PostgreSQL Flexible Server
- Neon
- Supabase-hosted PostgreSQL

## Returned capabilities

The snapshot can report PostgreSQL version, recovery state, WAL level, sender capacity, slot capacity, and provider constraints.

## Secret safety

The query does not read host names, connection strings, resource identifiers, passwords, or secret setting values.

The profile reports expectations, not enabled cloud features.
