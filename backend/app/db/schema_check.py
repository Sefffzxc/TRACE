"""Compare the database against the models, and say so plainly.

``Base.metadata.create_all`` creates tables that are missing. It does not
add a column to a table that already exists. So after the models gain a
column, a deployed database keeps the old shape and every query that
mentions the new column fails — as a 500, which the browser then reports
as a CORS error because the response escapes above the CORS middleware,
which sends whoever is debugging it to look at the CORS configuration.

That chain has cost real time more than once on this project. This module
breaks it at the first link: at startup the application asks the database
what columns it actually has, compares that with what the models expect,
and logs the difference as a list of column names and the one command that
fixes it. The mismatch is also served at /health, so it can be read without
shell access.

Nothing is altered here. Adding columns automatically would be worse than
the problem: it would paper over a schema that no longer matches the data
in it. The job of this module is to make the failure legible.
"""
from __future__ import annotations

import logging
from typing import Dict, List

from sqlalchemy import inspect

from app.db.session import Base, async_engine

log = logging.getLogger("lbca.schema")

# The state of the last check, so /health can report it without re-running.
STATE: dict = {"checked": False, "ok": True, "missing_tables": [], "missing_columns": {}}


def _expected() -> Dict[str, set]:
    """Every table and column the models declare."""
    import app.models  # noqa: F401  (registers the tables)

    return {
        name: {column.name for column in table.columns}
        for name, table in Base.metadata.tables.items()
    }


def _compare(sync_connection) -> dict:
    inspector = inspect(sync_connection)
    present = set(inspector.get_table_names())

    missing_tables: List[str] = []
    missing_columns: Dict[str, List[str]] = {}

    for table, columns in _expected().items():
        if table not in present:
            missing_tables.append(table)
            continue
        actual = {c["name"] for c in inspector.get_columns(table)}
        gap = sorted(columns - actual)
        if gap:
            missing_columns[table] = gap

    return {
        "missing_tables": sorted(missing_tables),
        "missing_columns": missing_columns,
    }


async def verify() -> dict:
    """Run the comparison and log the result. Never raises.

    A failure to check must not stop the application starting: a database
    that is briefly unreachable is a different problem, and turning it into
    a boot loop would hide it.
    """
    try:
        async with async_engine.connect() as conn:
            result = await conn.run_sync(_compare)
    except Exception as exc:  # noqa: BLE001 - reported, never raised
        log.warning("could not check the database schema: %s", exc)
        STATE.update({"checked": False, "ok": True, "error": str(exc)})
        return STATE

    ok = not result["missing_tables"] and not result["missing_columns"]
    STATE.update({"checked": True, "ok": ok, **result})

    if ok:
        log.info("database schema matches the models")
        return STATE

    log.error("=" * 68)
    log.error("DATABASE SCHEMA IS OUT OF DATE — requests that touch these")
    log.error("will fail with a 500, which the browser reports as a CORS error.")
    for table in result["missing_tables"]:
        log.error("  table missing entirely : %s", table)
    for table, columns in result["missing_columns"].items():
        log.error("  %-22s missing columns: %s", table, ", ".join(columns))
    log.error("")
    log.error("  Fix:  python seed.py --fresh-start")
    log.error("        python seed.py --demo        (optional demonstration data)")
    log.error("")
    log.error("  create_all adds missing TABLES but never adds a COLUMN to a")
    log.error("  table that already exists, which is why a redeploy alone")
    log.error("  does not clear this.")
    log.error("=" * 68)
    return STATE
