"""Add the columns the models have gained to an existing database.

``create_all`` never adds a column to a table that already exists, and
``seed.py --fresh-start`` fixes that by wiping everything. This is the
non-destructive alternative: every missing column that is nullable or has a
plain default is added with ``ALTER TABLE ... ADD COLUMN``, which leaves every
existing value untouched. A column that cannot be added without inventing
data (NOT NULL with no default, unique, primary key) is reported and skipped.

Running it
----------
    python add_missing_columns.py            # show what would change
    python add_missing_columns.py --apply    # change it

It uses DATABASE_URL like the API does, so on Render run it from the API
service's Shell tab.
"""
from __future__ import annotations

import asyncio
import sys
from typing import Dict, List, Optional

from sqlalchemy import text

from app.db.schema_check import _compare
from app.db.session import Base, async_engine


def _default_literal(column, dialect) -> Optional[str]:
    """The column's default as SQL, or None if it has no plain value."""
    default = column.default
    if default is None or not getattr(default, "is_scalar", False):
        return None
    value = default.arg
    if isinstance(value, bool):
        if dialect.name == "postgresql":
            return "TRUE" if value else "FALSE"
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    return None


def _plan(sync_connection, missing: Dict[str, List[str]]):
    dialect = sync_connection.dialect
    q = dialect.identifier_preparer
    statements: List[str] = []
    skipped: List[str] = []
    for table_name, names in missing.items():
        table = Base.metadata.tables[table_name]
        for name in names:
            column = table.c[name]
            default = _default_literal(column, dialect)
            if column.primary_key or column.unique or (
                not column.nullable and default is None
            ):
                skipped.append(f"{table_name}.{name}")
                continue
            ddl = (
                f"ALTER TABLE {q.format_table(table)} ADD COLUMN "
                f"{q.format_column(column)} {column.type.compile(dialect=dialect)}"
            )
            if default is not None:
                ddl += f" DEFAULT {default}"
            if not column.nullable:
                ddl += " NOT NULL"
            statements.append(ddl)
            if column.index:
                statements.append(
                    f"CREATE INDEX IF NOT EXISTS {q.quote(f'ix_{table_name}_{name}')} "
                    f"ON {q.format_table(table)} ({q.format_column(column)})"
                )
    return statements, skipped


def _run(sync_connection, apply: bool):
    result = _compare(sync_connection)
    if result["missing_tables"]:
        print("Missing tables (the API creates these itself on startup):",
              ", ".join(result["missing_tables"]))
    statements, skipped = _plan(sync_connection, result["missing_columns"])
    if not statements and not skipped:
        print("The database already matches the models. Nothing to do.")
        return
    for ddl in statements:
        print(("  applying: " if apply else "  would run: ") + ddl)
        if apply:
            sync_connection.execute(text(ddl))
    for name in skipped:
        print(f"  SKIPPED {name}: cannot be added without a value for existing "
              "rows; use seed.py --fresh-start")
    if not apply and statements:
        print("\nDry run only. Re-run with --apply to make these changes.")


async def main(apply: bool) -> None:
    import app.models  # noqa: F401  (registers the tables)

    async with async_engine.begin() as conn:
        await conn.run_sync(_run, apply)
    await async_engine.dispose()


if __name__ == "__main__":
    asyncio.run(main("--apply" in sys.argv[1:]))
