# PostGIS catalog diagnostics

The planned PostGIS tool reads spatial metadata. It does not execute spatial analysis functions.

The tool uses PostgreSQL core catalogs only. It uses bounded, read-only queries.

## Reported columns

The tool reports columns that use PostGIS-owned types.

It reports these types:

- `geometry`
- `geography`
- `raster`

For typmod columns, the tool reports the shape, SRID, and coordinate dimension.

For unconstrained columns, these values are not available from the typmod.

The tool does not call `ST_SRID`, `ST_NDims`, or `GeometryType`.

## Reported indexes

The tool reports indexes that use PostGIS-owned operator classes.

The result includes the access method, validity, readiness, predicate, expression, definition, operator classes, and relation options.

The tool preserves unknown operator classes. This behavior supports future PostGIS versions.

## Findings

The tool reports an error for an invalid spatial index.

The tool reports a warning for an index that is not ready.

The tool reports information for a partial index or an unknown operator class.

These findings describe catalog state only. They do not measure distance accuracy or query performance.

## Limits

The combined column and index limit is 500 items.

The tool rejects malformed rows and duplicate object identities.

The tool does not change indexes, data, settings, or extension state.
