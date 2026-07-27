# PostgreSQL extension-owned object inventory

`get_extension_objects` answers which PostgreSQL objects belong to one installed extension without invoking that extension's code. It uses only the core `pg_extension`, `pg_depend`, and `pg_identify_object` catalogs/functions.

## Contract

- The caller supplies an exact installed extension name.
- The extension identity is resolved from `pg_extension` and its installation schema.
- Extension membership is read from `pg_depend` rows with dependency type `e`.
- `pg_identify_object` provides PostgreSQL's canonical object type, schema, name, and identity.
- Object addresses preserve catalog OID, object OID, and sub-object ID.
- Results are deterministically ordered and capped at 500 objects with explicit truncation.
- Unknown future object classes are returned as PostgreSQL reports them; they are not discarded or coerced into a fixed application enum.

The implementation never executes extension-owned functions, imports extension client libraries, reads connection strings, or changes extension state. It works for built-in, third-party, provider-installed, and future extensions whose objects are registered through PostgreSQL's normal extension dependency machinery.

## Limits

An inventory describes catalog ownership, not runtime safety. Extension objects may include functions, access methods, background-worker configuration, event triggers, foreign-data wrappers, or other behavior. Use the extension capability profile and vendor documentation before executing extension-owned operations.

## Local validation

The generic integration contract inventories the built-in `plpgsql` extension on real PostgreSQL. The PostgreSQL 14–18 compatibility matrix runs the same core-catalog contract on every supported major.
