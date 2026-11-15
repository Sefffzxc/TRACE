"""Remove obsolete academic codes while preserving IDs and linked records.

Run with the API stopped and DATABASE_URL set to the intended database:
    python migrate_academic_names.py          # inspect only
    python migrate_academic_names.py --apply  # apply after checking a backup

No application models are imported and no database is created or reseeded.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from pathlib import Path
import re

from sqlalchemy import inspect, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import create_async_engine


TABLES = (
    ("sections", "section_id", "section_code", "name", "uq_section_name_per_grade"),
    ("subjects", "subject_id", "subject_code", "subject_name", "uq_subject_name_per_grade"),
)


class MigrationError(RuntimeError):
    pass


def _tokens(sql: str) -> list[str]:
    # Ignore string literals when deciding whether DDL references a column.
    sql = re.sub(r"'(?:''|[^'])*'", "''", sql)
    return [token.lower() for token in re.findall(r"[A-Za-z_][A-Za-z_0-9]*", sql)]


def _ddl_parts(body: str) -> list[str]:
    """Split a SQLite CREATE TABLE body outside quoted text/parentheses."""
    parts, start, depth, quote, index = [], 0, 0, None, 0
    while index < len(body):
        char = body[index]
        if quote:
            if char == quote:
                if quote != "]" and index + 1 < len(body) and body[index + 1] == quote:
                    index += 1
                else:
                    quote = None
        elif char in "'\"`[":
            quote = "]" if char == "[" else char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append(body[start:index].strip())
            start = index + 1
        index += 1
    if quote or depth:
        raise MigrationError("Unsupported SQLite table definition; no changes were applied.")
    parts.append(body[start:].strip())
    return parts


def _plan(conn):
    inspector = inspect(conn)
    plan = []
    for table, pk, code, name, constraint in TABLES:
        if not inspector.has_table(table):
            raise MigrationError(f"Missing {table} table. Check the selected database and its school migration.")
        columns = [column["name"] for column in inspector.get_columns(table)]
        if not {pk, "school_id", "grade_level_id", name}.issubset(columns):
            raise MigrationError(f"{table} does not have the expected multi-school structure.")
        q = conn.dialect.identifier_preparer.quote
        rows = conn.execute(text(
            f"SELECT {q(pk)}, school_id, grade_level_id, {q(name)} FROM {q(table)}"
        )).all()
        groups = defaultdict(list)
        for identity, school_id, grade_id, label in rows:
            if not isinstance(label, str) or not label.strip():
                raise MigrationError(f"{table} row {identity} has a blank name; correct it before applying.")
            groups[(school_id, grade_id, label.strip().casefold())].append(identity)
        for (school_id, grade_id, _), identities in groups.items():
            if len(identities) > 1:
                raise MigrationError(
                    f"Duplicate names in {table}: school {school_id}, grade {grade_id}, "
                    f"row IDs {identities}. Rename these records deliberately before applying."
                )
        desired = ["school_id", "grade_level_id", name]
        unique_items = inspector.get_unique_constraints(table) + [
            item for item in inspector.get_indexes(table) if item.get("unique")
        ]
        has_unique = any(item.get("column_names") == desired for item in unique_items)
        if any(item.get("name") == constraint and item.get("column_names") != desired for item in unique_items):
            raise MigrationError(f"Unexpected definition for {constraint}; inspect it before applying.")
        plan.append(dict(
            table=table, pk=pk, code=code, name=name, constraint=constraint,
            columns=columns, remove_code=code in columns, add_unique=not has_unique, rows=len(rows),
        ))
    return plan


def _sqlite_rebuild(conn, item):
    table, code = item["table"], item["code"]
    q = conn.dialect.identifier_preparer.quote
    original = conn.execute(text("SELECT sql FROM sqlite_master WHERE type='table' AND name=:name"), {"name": table}).scalar_one()
    start, end = original.index("("), original.rfind(")")
    definitions = []
    for definition in _ddl_parts(original[start + 1:end]):
        identifiers = _tokens(definition)
        if code in identifiers:
            if identifiers[0] not in {code, "constraint", "unique", "check", "foreign"}:
                raise MigrationError(f"Another column in {table} depends on {code}; inspect it manually.")
            continue
        definitions.append(definition)
    if item["add_unique"]:
        definitions.append(
            f'CONSTRAINT {q(item["constraint"])} UNIQUE (school_id, grade_level_id, {q(item["name"])})'
        )
    objects = conn.execute(text(
        "SELECT type, name, sql FROM sqlite_master WHERE tbl_name=:table "
        "AND type IN ('index','trigger') AND sql IS NOT NULL"
    ), {"table": table}).all()
    for kind, object_name, sql in objects:
        if kind == "trigger" and code in _tokens(sql):
            raise MigrationError(f"Trigger {object_name} depends on {code}; review it before applying.")
    temporary = f"_trace_new_{table}"
    if inspect(conn).has_table(temporary):
        raise MigrationError(f"Unexpected table {temporary}; inspect it before applying.")
    conn.exec_driver_sql(f"CREATE TABLE {q(temporary)} (" + ", ".join(definitions) + ")" + original[end + 1:])
    columns = ", ".join(q(column) for column in item["columns"] if column != code)
    conn.exec_driver_sql(f"INSERT INTO {q(temporary)} ({columns}) SELECT {columns} FROM {q(table)}")
    copied = conn.exec_driver_sql(f"SELECT COUNT(*) FROM {q(temporary)}").scalar_one()
    if copied != item["rows"]:
        raise MigrationError(f"Row-count mismatch while copying {table}.")
    conn.exec_driver_sql(f"DROP TABLE {q(table)}")
    conn.exec_driver_sql(f"ALTER TABLE {q(temporary)} RENAME TO {q(table)}")
    for _, _, sql in objects:
        if code not in _tokens(sql):
            conn.exec_driver_sql(sql)


def migrate_connection(conn, *, apply=False):
    """Migration entry point usable with SQLAlchemy's sync or async engines."""
    dialect = conn.dialect.name
    if dialect not in {"sqlite", "postgresql"}:
        raise MigrationError("Only SQLite and PostgreSQL are supported.")
    if not apply:
        return _plan(conn)
    if conn.in_transaction():
        raise MigrationError("Use a fresh connection outside an existing transaction.")

    foreign_keys = None
    try:
        if dialect == "sqlite":
            foreign_keys = conn.exec_driver_sql("PRAGMA foreign_keys").scalar_one()
            conn.commit()
            conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
            conn.commit()
            conn.exec_driver_sql("BEGIN IMMEDIATE")
            if conn.exec_driver_sql("PRAGMA foreign_key_check").first() is not None:
                raise MigrationError("Existing foreign-key inconsistencies must be resolved before applying.")
            inspector = inspect(conn)
            for table in inspector.get_table_names():
                for key in inspector.get_foreign_keys(table):
                    for target, _, code, _, _ in TABLES:
                        if key.get("referred_table") == target and code in key.get("referred_columns", []):
                            raise MigrationError(f"{table} references a legacy code; inspect its foreign key manually.")
        else:
            conn.begin()
            conn.exec_driver_sql("SET LOCAL lock_timeout = '15s'")
            conn.exec_driver_sql("LOCK TABLE sections, subjects IN ACCESS EXCLUSIVE MODE")

        plan = _plan(conn)
        q = conn.dialect.identifier_preparer.quote
        for item in plan:
            table = q(item["table"])
            columns = f'school_id, grade_level_id, {q(item["name"])}'
            if item["remove_code"]:
                if dialect == "sqlite":
                    _sqlite_rebuild(conn, item)
                    continue
                conn.exec_driver_sql(f'ALTER TABLE {table} DROP COLUMN {q(item["code"])}')
            if item["add_unique"]:
                if dialect == "sqlite":
                    conn.exec_driver_sql(f'CREATE UNIQUE INDEX {q(item["constraint"])} ON {table} ({columns})')
                else:
                    conn.exec_driver_sql(f'ALTER TABLE {table} ADD CONSTRAINT {q(item["constraint"])} UNIQUE ({columns})')
        if dialect == "sqlite":
            if conn.exec_driver_sql("PRAGMA foreign_key_check").first() is not None:
                raise MigrationError("Foreign-key validation failed; all migration changes were rolled back.")
            if conn.exec_driver_sql("PRAGMA quick_check").scalar_one() != "ok":
                raise MigrationError("SQLite integrity validation failed; changes were rolled back.")
        conn.commit()
        return plan
    except Exception:
        conn.rollback()
        raise
    finally:
        if foreign_keys is not None:
            conn.exec_driver_sql(f"PRAGMA foreign_keys={int(foreign_keys)}")
            conn.commit()


async def _run(apply):
    from app.core.config import settings

    engine = create_async_engine(settings.sqlalchemy_url, connect_args=settings.db_connect_args)
    try:
        if engine.dialect.name == "sqlite" and engine.url.database not in (None, "", ":memory:"):
            if not Path(engine.url.database).is_file():
                raise MigrationError("SQLite database does not exist. Check DATABASE_URL; no file was created.")
        async with engine.connect() as conn:
            plan = await conn.run_sync(lambda connection: migrate_connection(connection, apply=apply))
        for item in plan:
            changes = []
            if item["remove_code"]:
                changes.append("remove obsolete code column")
            if item["add_unique"]:
                changes.append("enforce unique names within each grade")
            print(f'{item["table"]}: {item["rows"]} rows; ' + (", ".join(changes) or "already up to date"))
        print("Migration applied; row IDs and links preserved." if apply else "Inspection only. Back up the database and stop the API before running with --apply.")
    finally:
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Apply the migration after a verified database backup.")
    args = parser.parse_args()
    try:
        asyncio.run(_run(args.apply))
    except MigrationError as exc:
        print(f"Migration stopped: {exc}")
        return 1
    except SQLAlchemyError as exc:
        print(f"Migration failed ({type(exc).__name__}). Changes were rolled back; inspect database access and schema privately.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
