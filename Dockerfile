FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app

COPY pyproject.toml uv.lock README.md LICENSE ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim-bookworm AS runtime

RUN groupadd --gid 1000 app \
    && useradd --uid 1000 --gid app --shell /bin/bash --create-home app

WORKDIR /app
COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --chown=app:app docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod 0555 /app/docker-entrypoint.sh

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

ARG TARGETPLATFORM
LABEL org.opencontainers.image.description="pgsql-mcp - PostgreSQL MCP Server (${TARGETPLATFORM})" \
      org.opencontainers.image.source="https://github.com/surajmandalcell/pgsql-mcp" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.vendor="surajmandalcell"

USER app
EXPOSE 8000
ENTRYPOINT ["/app/docker-entrypoint.sh", "pgsql-mcp"]
CMD []
