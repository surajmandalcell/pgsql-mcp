# PostgreSQL extension capability profiles

PostgreSQL extensions can add types, catalogs, operators, access methods, background workers, and external side effects. `pgsql-mcp` therefore treats installed extension metadata as a runtime capability contract rather than assuming that every PostgreSQL-compatible deployment has the same surface.

The `get_extension_profiles` tool reads only `pg_catalog.pg_extension`, `pg_catalog.pg_namespace`, and `pg_catalog.pg_available_extensions`. It never calls extension-owned functions, loads extension client libraries, changes server state, or exposes connection strings.

## Support tiers

| Family | Extension names | Current contract |
|---|---|---|
| PostGIS | `postgis`, `postgis_raster`, `postgis_topology`, `postgis_tiger_geocoder` | Dynamic type preservation, spatial catalog and index metadata compatibility |
| TimescaleDB | `timescaledb`, `timescaledb_toolkit` | Hypertable, chunk, and continuous-aggregate catalog compatibility |
| Citus | `citus`, `citus_columnar` | Distribution, shard, and worker catalog compatibility |
| pgvector | `vector` | Vector type preservation and vector index metadata compatibility |
| HypoPG | `hypopg` | Specialized hypothetical-index support through existing plan and workload tools |
| pg_stat_statements | `pg_stat_statements` | Specialized workload-statistics support through existing workload tools |
| Any other extension | Any valid installed or available extension name | Generic catalog presence and unknown-type preservation |

`catalog_and_type_compatible` does not claim that pgsql-mcp can administer every extension-specific object. It means the generic OID-backed catalog and loss-aware type layers retain extension objects without flattening or rejecting them. Specialized mutating extension operations remain unavailable unless they receive their own reviewed, tested bounded context.

## Installed and available inventory

By default, `get_extension_profiles` returns installed extensions only. Set `include_available=true` to include extensions visible through `pg_available_extensions` but not installed. Results are deterministic and capped at 500 profiles. Installed extensions are always returned before available-only entries, and truncation is explicit.

Each profile contains:

- exact normalized extension name;
- installed and default version strings without semantic reinterpretation;
- installation schema when installed;
- PostgreSQL-provided comment when available;
- known extension family or `other`;
- support tier;
- generic capabilities;
- specialized pgsql-mcp tools, when they already exist.

Unknown future extensions are preserved as `other` with `generic_catalog` support. This allows new or provider-specific extensions to remain inspectable before a dedicated adapter is added.

## Security boundaries

Extension installation, upgrade, removal, configuration, and extension-owned function execution are outside this read-only inventory. Those operations can require elevated privileges or produce effects that PostgreSQL transactions cannot reverse. They must use reviewed migration or maintenance workflows when pgsql-mcp explicitly supports them.

A least-privilege role may see fewer available extensions or comments. The returned inventory reflects the connected role's real catalog visibility and never attempts to bypass it.
