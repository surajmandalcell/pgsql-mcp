# Runtime observability

The full `pgsql-mcp` entry point records bounded, privacy-preserving process metrics at FastMCP's central tool-call boundary. The reliability-focused `pgsql-mcp-lite` profile remains unchanged and does not import the observability or full-server modules.

## Data contract

The registry stores only:

- registered tool name;
- aggregate outcome (`success`, `error`, `denied`, `cancelled`, or `unknown`);
- active-call count;
- cumulative duration histogram;
- explicit truncation count;
- confirmed rollback count.

It never stores MCP arguments, SQL text, parameter values, database or schema identifiers, returned rows, exception messages, connection strings, authentication headers, or client identity. Tool-label cardinality is capped; additional labels collapse into `__other__`.

Every in-flight invocation receives a cryptographically random correlation ID through a `contextvars` boundary. The ID is available to structured logging or an optional exporter through `current_correlation_id()` and is cleared when the invocation completes.

## MCP inspection

The full server exposes `get_runtime_metrics`. It returns the deterministic aggregate JSON snapshot and is safe in restricted mode. The metrics tool itself receives the same central instrumentation as every other tool.

## Prometheus and health endpoints

The HTTP exporter is disabled unless `METRICS_PORT` is set.

```bash
METRICS_PORT=9464 pgsql-mcp postgresql://localhost/app
```

The default bind address is `127.0.0.1`. Two read-only endpoints are available:

- `GET /metrics` — Prometheus text exposition;
- `GET /healthz` — a minimal `{"status":"ok"}` response.

Only `GET` and `HEAD` are accepted. Responses disable caching and content sniffing. Request paths and bodies are not logged.

A non-loopback bind is rejected unless explicitly enabled:

```bash
METRICS_HOST=0.0.0.0 \
METRICS_PORT=9464 \
ALLOW_REMOTE_METRICS=true \
pgsql-mcp postgresql://localhost/app
```

Remote exposure has no built-in authentication and must be placed behind an authenticated, encrypted, rate-limited reverse proxy or a private network policy. Loopback binding is the recommended default.

## Local validation

```bash
uv run pytest -q tests/unit/observability
uv run ruff format --check .
uv run ruff check .
uv run pyright
```
