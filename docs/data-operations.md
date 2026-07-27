# Typed data operations

Typed data tools provide structured read and write requests.

They do not accept raw mutation SQL.

## Relations and columns

Each request uses an exact schema and relation name.

The repository validates the live relation, columns, privileges, generated columns, identity columns, and usable unique keys.

## Filters

Filters use bounded structured conditions.

The service combines all required conditions with `AND`.

It can also combine a bounded optional group with `OR`.

Values use native PostgreSQL parameters.

## Pagination

Select requests use keyset pagination.

The order must include a complete primary key or non-partial unique key.

Cursors are bound to the relation and order.

## Write guards

Each mutation requires `max_affected_rows`.

A request can also require `expected_rows`.

Update and delete requests require filters.

Optimistic predicates can protect versioned rows.

## Transaction rules

Select operations use repeatable-read read-only transactions.

Mutations use serializable read-write transactions.

A mutation rolls back when the service cannot return a safe bounded result.
