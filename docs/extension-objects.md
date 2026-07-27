# Extension-owned object inventory

`get_extension_objects` lists PostgreSQL objects that belong to one installed extension.

## Data source

The repository reads `pg_extension` and `pg_depend`.

It uses the core `pg_identify_object` function for canonical object descriptions.

## Returned identity

Each object can include:

- object type
- schema
- object name
- canonical identity
- catalog name
- object OID
- sub-object ID

Unknown future object types remain unchanged.

## Limits

The result contains at most 500 objects.

A truncation flag reports additional objects.

Duplicate object addresses cause an error.

## Safety

The tool does not execute extension-owned functions.

It does not import extension libraries.

It does not change extension state.
