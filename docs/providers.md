# PostgreSQL deployment provider profiles

`pgsql-mcp` treats deployment identity as policy input, not as a branding guess. The provider profile reads only standard PostgreSQL settings, boolean catalog markers, and a fixed allowlist of administrative role names. It never reads connection strings, host names, cloud resource identifiers, secret setting values, or extension-owned functions.

## Classification contract

Automatic detection requires a strong marker such as a provider-specific setting prefix, administrative role, or explicit server-version marker. When no strong marker is visible, the result is `unknown`; absence of managed-service markers does not prove that a server is self-hosted.

Supported explicit profiles are:

- `upstream`
- `generic_managed`
- `aws_rds`
- `aws_aurora`
- `google_cloud_sql`
- `google_alloydb`
- `azure_flexible_server`
- `neon`
- `supabase_hosted`

Use `provider_hint=auto` for conservative detection. An explicit hint is authoritative but the response includes a warning when it conflicts with a strong observed marker.

## Runtime capabilities

Every snapshot includes standard PostgreSQL facts:

- server version number;
- primary or recovery role;
- `wal_level`;
- maximum WAL senders;
- maximum replication slots;
- whether the observed settings currently satisfy the minimum logical-replication configuration.

The provider constraints describe administrative expectations rather than claiming that a feature is enabled. Extension installation, logical replication, backup, failover, and privileged maintenance remain dependent on the provider plan, control-plane configuration, database role, and PostgreSQL version.

## Security boundary

The profile is read-only and safe for restricted mode. It does not attempt administrative actions, inspect provider APIs, read credential-bearing settings, or infer an account/project identifier. Provider documentation remains the source of truth for enabling a capability; this tool reports the active database evidence available to the connected role.
