# Publishing

Use this procedure to publish a release from a verified `main` commit.

GitHub Actions validate package and container builds after each update to `main`.

GitHub Actions do not publish packages or container images.

## Release requirements

- All `main` jobs must pass.
- The working tree must be clean.
- The local commit must match the selected `main` commit.
- The version must match the release tag.
- The release notes must describe user-visible changes.
- Package and container budgets must pass.
- PostgreSQL compatibility jobs must pass.

## Build the package

```bash
uv sync --all-extras
uv build
```

Inspect the wheel and source archive.

Do not publish credentials, transfer files, or temporary workflows.

## Test the package

1. Create a clean virtual environment.
2. Install the built wheel.
3. Run `pgsql-mcp --help`.
4. Run `pgsql-mcp-lite --help`.
5. Run `pgsql-mcp-ha --help`.
6. Run a read-only smoke test against a disposable PostgreSQL database.

## Publish the Python package

Configure an approved PyPI token or trusted local publishing identity.

```bash
uv publish dist/*
```

Verify the public package name, version, files, license, and description.

## Publish the container image

Build the image from the same verified commit.

```bash
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  --tag "$DOCKERHUB_USERNAME/pgsql-mcp:$VERSION" \
  --tag "$DOCKERHUB_USERNAME/pgsql-mcp:latest" \
  --push \
  .
```

Verify the public image digest and platform list.

## Create the release

Create a signed tag only after package and container verification.

Create the release notes from the same commit.
