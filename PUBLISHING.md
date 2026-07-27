# Publishing

Use this procedure to publish a release.

## Release requirements

- `main` must be green.
- The working tree must be clean.
- The version must match the release tag.
- The changelog text must describe user-visible changes.
- Package and container budgets must pass.
- PostgreSQL compatibility jobs must pass.

## Build the package

```bash
uv sync --all-extras
uv build
```

Inspect the generated wheel and source archive.

Do not publish a package that contains credentials, transfer files, or temporary workflows.

## Test the package

1. Create a clean virtual environment.
2. Install the built wheel.
3. Run `pgsql-mcp --help`.
4. Run `pgsql-mcp-lite --help`.
5. Run `pgsql-mcp-ha --help`.
6. Run a read-only smoke test against a disposable PostgreSQL database.

## Publish

Create the signed release tag only after all checks pass.

Publish the Python package through the approved release workflow.

Publish the container image through the approved container workflow.

Verify the public package metadata after publication.
