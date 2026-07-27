# Repository instructions

Use the current `main` branch as the integration base.

## Engineering rules

- Use domain-driven design for new bounded contexts.
- Write failing tests before production code.
- Use native PostgreSQL parameters for values.
- Use trusted identifier composition for identifiers.
- Keep restricted mode structurally read-only.
- Preserve the lite and HA import boundaries.
- Report uncertain commit outcomes as unknown.
- Do not claim rollback for nontransactional operations.
- Keep results bounded by rows and encoded bytes.
- Use ASD-STE100 style in Markdown files.

## Validation

Run Ruff, Pyright, pytest, changed-line coverage, mutation tests, and required PostgreSQL matrices.

Do not merge temporary transfer workflows or payload files.
