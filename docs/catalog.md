# PostgreSQL catalog model

Catalog tools use live PostgreSQL object identities.

## Identity

The model keeps object OIDs, schema names, object names, and relation kinds.

It rechecks live catalog state before sensitive operations.

Names alone do not establish identity.

## Relation details

Relation inspection can report:

- columns and type OIDs
- constraints and indexes
- partition relationships
- triggers and rules
- row-level security
- policies and grants
- persistence and relation kind

## Type details

Type inspection supports built-in and user-defined types.

It preserves arrays, domains, enums, composites, ranges, multiranges, pseudo-types, and unknown extension types.

Unknown values keep their PostgreSQL OID and a tagged representation.

## Safety

Catalog queries use fixed SQL and native parameters.

The tools do not execute functions owned by inspected extensions.
