"""Repair psycopg result-set handling and BEGIN rollback windows, then self-delete."""

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    content = target.read_text()
    if content.count(old) != 1:
        raise RuntimeError(f"expected exactly one driver-fix target in {path}")
    target.write_text(content.replace(old, new, 1))


replace_once(
    "src/postgres_mcp/sql/sql_driver.py",
    '''                if force_readonly:
                    await cursor.execute("BEGIN TRANSACTION READ ONLY")
                    transaction_started = True

                if params:
''',
    '''                transaction_started = True
                if force_readonly:
                    await cursor.execute("BEGIN TRANSACTION READ ONLY")

                if params:
''',
)

replace_once(
    "src/postgres_mcp/sql/sql_driver.py",
    '''                while await cursor.nextset():
                    pass
''',
    '''                while cursor.nextset():
                    pass
''',
)

replace_once(
    "src/postgres_mcp/sql/sql_driver.py",
    '''            async with connection.cursor(row_factory=dict_row) as cursor:
                await cursor.execute("BEGIN TRANSACTION READ ONLY" if force_readonly else "BEGIN TRANSACTION")
                transaction_started = True
''',
    '''            async with connection.cursor(row_factory=dict_row) as cursor:
                transaction_started = True
                await cursor.execute("BEGIN TRANSACTION READ ONLY" if force_readonly else "BEGIN TRANSACTION")
''',
)

replace_once(
    "src/postgres_mcp/sql/sql_driver.py",
    '''            async with connection.cursor() as control_cursor:
                access_clause = "READ ONLY" if read_only else "READ WRITE"
                await control_cursor.execute(f"BEGIN ISOLATION LEVEL {isolation.sql} {access_clause}")
                transaction_started = True
''',
    '''            async with connection.cursor() as control_cursor:
                access_clause = "READ ONLY" if read_only else "READ WRITE"
                transaction_started = True
                await control_cursor.execute(f"BEGIN ISOLATION LEVEL {isolation.sql} {access_clause}")
''',
)

Path(__file__).unlink()
