"""Apply the focused parser and timeout fixes from PR review, then self-delete."""

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    content = target.read_text()
    if content.count(old) != 1:
        raise RuntimeError(f"expected exactly one review-fix target in {path}")
    target.write_text(content.replace(old, new, 1))


replace_once(
    "src/postgres_mcp/sql/transaction.py",
    """        if state == "single_quote":
            output.append(current)
            if current == "\\\\" and following:
                output.append(following)
                index += 2
                continue
            if current == "'":
                if following == "'":
                    output.append(following)
                    index += 2
                    continue
                state = "normal"
            index += 1
            continue
""",
    """        if state in {"single_quote", "escape_single_quote"}:
            output.append(current)
            if state == "escape_single_quote" and current == "\\\\" and following:
                output.append(following)
                index += 2
                continue
            if current == "'":
                if following == "'":
                    output.append(following)
                    index += 2
                    continue
                state = "normal"
            index += 1
            continue
""",
)

replace_once(
    "src/postgres_mcp/sql/transaction.py",
    """        if current == "'":
            output.append(current)
            state = "single_quote"
            index += 1
            continue
""",
    """        previous = sql[index - 1] if index > 0 else ""
        if (
            current in {"E", "e"}
            and following == "'"
            and not (previous.isalnum() or previous in {"_", "$"})
        ):
            output.extend((current, following))
            state = "escape_single_quote"
            index += 2
            continue
        if current == "'":
            output.append(current)
            state = "single_quote"
            index += 1
            continue
""",
)

replace_once(
    "src/postgres_mcp/sql/transaction.py",
    """    if state in {"single_quote", "double_quote", "block_comment", "dollar_quote"}:
""",
    """    if state in {"single_quote", "escape_single_quote", "double_quote", "block_comment", "dollar_quote"}:
""",
)

replace_once(
    "src/postgres_mcp/server.py",
    """from .sql import parse_single_statement  # noqa: E402
""",
    "",
)

replace_once(
    "src/postgres_mcp/server.py",
    """        SafeQueryExecutor(
            get_base_sql_driver(),
            timeout_seconds=current_query_timeout,
        ).validator.validate_query(sql, parameter_count=0)
""",
    """        await SafeQueryExecutor(
            get_base_sql_driver(),
            timeout_seconds=current_query_timeout,
        ).validate_query(sql, parameter_count=0)
""",
)

replace_once(
    "src/postgres_mcp/server.py",
    """        parse_single_statement(sql, parameter_count=len(params) if params is not None else 0)
""",
    "",
)

Path(__file__).unlink()
