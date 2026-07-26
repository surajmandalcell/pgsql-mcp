# Performance and release budgets

`pgsql-mcp` treats startup cost, memory, package size, and container size as blocking release properties rather than informal goals.

## Blocking budgets

The `Performance release budgets` workflow builds the wheel and minimal runtime image, then measures independent cold Python processes. The current upper bounds are:

| Measurement | Maximum |
|---|---:|
| Core package cold process | 750 ms |
| Core package peak RSS | 64 MiB |
| Lite server module cold process | 1,500 ms |
| Lite server module peak RSS | 128 MiB |
| Compressed wheel | 2 MiB |
| Uncompressed Docker image | 300 MiB |

The core import must remain lazy: it may not load the full server, lite server, migrations, typed-data operations, or health suite. The lite import may not load migrations, typed writes, maintenance, health, or LLM modules.

## Local reproduction

```bash
uv sync --frozen --all-extras
uv run pytest -q tests/unit/quality/test_release_budgets.py
uv build --wheel --out-dir dist
docker build --tag pgsql-mcp:release-budget .
uv run python scripts/check_release_budgets.py \
  --wheel "$(find dist -maxdepth 1 -type f -name '*.whl' -print -quit)" \
  --image pgsql-mcp:release-budget \
  --json-output release-budget-report.json
```

Each measurement is emitted as JSON and retained as a workflow artifact. A budget may be tightened after measured improvements. Raising one requires an explicit rationale and review; it must never happen merely to silence a regression.
