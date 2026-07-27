# Explain plan module

This module validates and inspects PostgreSQL `EXPLAIN` plans.

## Safety

Restricted mode does not permit `EXPLAIN ANALYZE`.

The module validates the supplied read-only statement before it requests a plan.

Hypothetical index support requires HypoPG.

## Main responsibilities

- build safe `EXPLAIN` options
- parse JSON plans
- present important plan nodes
- compare plans with hypothetical indexes
- keep plan output bounded

Do not add direct write execution to this module.
