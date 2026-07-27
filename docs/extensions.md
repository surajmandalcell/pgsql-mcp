# Extension capability profiles

`get_extension_profiles` reports installed and optionally available extensions.

## Known families

The server recognizes:

- PostGIS
- TimescaleDB
- Citus
- pgvector
- HypoPG
- `pg_stat_statements`

Unknown extensions remain generic profiles.

## Support levels

A profile can report catalog and type compatibility.

A profile can also list specialized tools that already exist.

The profile does not claim complete runtime support.

## Safety

The repository reads `pg_extension`, `pg_namespace`, and `pg_available_extensions`.

It does not import extension client libraries.

It does not execute extension-owned functions.

Results are capped at 500 entries and report truncation.
