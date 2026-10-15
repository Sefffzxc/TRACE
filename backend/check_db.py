"""Ask the database what it actually has, and compare it with the models.

    python check_db.py

Reads DATABASE_URL the same way the application does, so point it at
whichever database you want to inspect:

    DATABASE_URL="postgresql://..." python check_db.py

Prints the tables and columns the models expect but the database does not
have, and nothing else. It never writes.

This exists because the failure it detects is almost unreadable from the
outside. ``create_all`` creates tables that are missing but never adds a
column to a table that already exists, so after the models gain a column
the deployed database keeps the old shape. Every query mentioning the new
column then fails with a 500, the error response is generated above the
CORS middleware so it carries no Access-Control-Allow-Origin header, and
the browser reports a CORS problem — sending whoever is debugging it to
look at the one piece of configuration that was never wrong.
"""
from __future__ import annotations

import asyncio
import sys

from sqlalchemy import inspect

import app.models  # noqa: F401  (registers every table)
from app.core.config import settings
from app.db.session import Base, async_engine


def _compare(sync_connection) -> tuple[list, dict, list]:
    inspector = inspect(sync_connection)
    present = set(inspector.get_table_names())

    missing_tables, missing_columns, extra_columns = [], {}, []
    for name, table in Base.metadata.tables.items():
        expected = {column.name for column in table.columns}
        if name not in present:
            missing_tables.append(name)
            continue
        actual = {c["name"] for c in inspector.get_columns(name)}
        gap = sorted(expected - actual)
        if gap:
            missing_columns[name] = gap
        # Columns the database has and the models no longer declare. Not a
        # fault — a dropped column stays until the table is rebuilt — but
        # worth seeing, because it tells you the table predates a change.
        stale = sorted(actual - expected)
        if stale:
            extra_columns.append((name, stale))
    return sorted(missing_tables), missing_columns, extra_columns


async def main() -> int:
    # Shown so there is no doubt which database was inspected, with the
    # password removed: this gets pasted into chats and issue trackers.
    url = settings.sqlalchemy_url
    if "@" in url:
        head, tail = url.split("@", 1)
        scheme = head.split("://", 1)[0]
        url = f"{scheme}://***@{tail}"
    print(f"database: {url}\n")

    try:
        async with async_engine.connect() as conn:
            missing_tables, missing_columns, extra_columns = await conn.run_sync(_compare)
    except Exception as exc:  # noqa: BLE001
        print(f"Could not connect: {exc}")
        return 2
    finally:
        await async_engine.dispose()

    if not missing_tables and not missing_columns:
        print("The database matches the models. Nothing is missing.")
        if extra_columns:
            print("\nColumns the database still has that the models no longer")
            print("declare (harmless, but they date the table):")
            for table, columns in extra_columns:
                print(f"  {table}: {', '.join(columns)}")
        return 0

    print("OUT OF DATE — these are what the models expect and the database")
    print("does not have. Any request touching them fails with a 500, which")
    print("the browser reports as a CORS error.\n")
    for table in missing_tables:
        print(f"  table missing entirely : {table}")
    for table, columns in missing_columns.items():
        print(f"  {table:24s} missing: {', '.join(columns)}")
    print("\nFix:")
    print("  python seed.py --fresh-start      empty and rebuild")
    print("  python seed.py --demo             demonstration data (optional)")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))