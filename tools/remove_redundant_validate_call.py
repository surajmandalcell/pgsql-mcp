"""Remove redundant Pydantic wrappers that FastMCP cannot introspect reliably."""

from pathlib import Path

path = Path("src/postgres_mcp/server.py")
content = path.read_text()

import_line = "from pydantic import validate_call\n"
if content.count(import_line) != 1:
    raise RuntimeError("expected exactly one validate_call import")
content = content.replace(import_line, "", 1)

for decorated_function in ("analyze_workload_indexes", "analyze_query_indexes"):
    marker = f"@validate_call\nasync def {decorated_function}("
    if content.count(marker) != 1:
        raise RuntimeError(f"expected exactly one validate_call wrapper for {decorated_function}")
    content = content.replace(marker, f"async def {decorated_function}(", 1)

path.write_text(content)
Path(__file__).unlink()
