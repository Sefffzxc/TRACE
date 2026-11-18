"""Permanently remove explicitly selected test schools and their database records.

Take a verified backup and stop the API before applying. No schema changes,
initialization, seeding, or sequence resets occur. Inspect school IDs first:

    python cleanup_test_schools.py --list
    python cleanup_test_schools.py --school-id 4 --school-id 9
    python cleanup_test_schools.py --school-id 4 --school-id 9 --apply --confirm-school-ids 4,9

The default is read-only. School names are deliberately not selection criteria:
different schools, including old and new registrations, can share a name.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from pathlib import Path

from sqlalchemy import MetaData, and_, delete, func, inspect, or_, select, tuple_, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import create_async_engine


class CleanupError(RuntimeError):
    """A safe, operator-facing error without database credentials or SQL."""


# These columns attribute an action; they do not establish ownership of a row.
# History belonging to a retained school/account must survive deletion of its
# actor. Unknown relationships are inspected as owning relationships instead.
_ACTOR_COLUMNS = {
    ("application_events", "actor_id"),
    ("email_correction_challenges", "actor_id"),
    ("audit_logs", "admin_id"),
    ("students", "created_by"),
    ("student_enrollments", "enrolled_by"),
    ("student_paces", "recorded_by"),
    ("class_record_setups", "updated_by"),
    ("data_quality_logs", "teacher_id"),
}
_AUTH_TABLES = {
    "staff_devices", "sessions", "otp_codes", "password_resets", "resubmission_challenges",
    "email_correction_challenges",
}


def _reflect(conn):
    if conn.dialect.name not in {"sqlite", "postgresql"}:
        raise CleanupError("Only SQLite and PostgreSQL are supported.")
    metadata = MetaData()
    metadata.reflect(bind=conn)
    tables = {table.name: table for table in metadata.tables.values()}
    for name, columns in {
        "schools": {"school_id", "name", "city", "status"},
        "staff": {"id", "school_id", "email", "role"},
    }.items():
        if name not in tables or not columns.issubset(tables[name].c.keys()):
            raise CleanupError(f"Missing or unexpected {name} table. Check the selected database.")
    return metadata, tables


def _school_list(conn, tables):
    schools, staff = tables["schools"], tables["staff"]
    people = defaultdict(list)
    counts = defaultdict(int)
    for row in conn.execute(select(staff.c.school_id, staff.c.email, staff.c.role)).mappings():
        counts[row.school_id] += 1
        if row.role == "admin":
            people[row.school_id].append(row.email)
    students = {}
    if "students" in tables:
        table = tables["students"]
        if "school_id" not in table.c:
            raise CleanupError("The students table has no school ownership column; inspect it before cleanup.")
        students = dict(conn.execute(select(table.c.school_id, func.count())
                                    .group_by(table.c.school_id)).all())
    return [dict(school_id=row.school_id, name=row.name, city=row.city, status=row.status,
                 principal_emails=sorted(people[row.school_id]),
                 staff_count=counts[row.school_id], student_count=students.get(row.school_id, 0))
            for row in conn.execute(select(schools.c.school_id, schools.c.name, schools.c.city,
                                           schools.c.status).order_by(schools.c.school_id)).mappings()]


def list_schools(conn):
    """Read-only school identity/count summary; never lists student details."""
    return _school_list(conn, _reflect(conn)[1])


def _ids(school_ids):
    values = list(school_ids)
    if not values or any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in values):
        raise CleanupError("Select one or more explicit positive school IDs.")
    if len(values) != len(set(values)):
        raise CleanupError("Each school ID must be selected only once.")
    return sorted(values)


def _identity(table):
    return list(table.primary_key.columns)


def _where(table, keys):
    columns = _identity(table)
    if not columns:
        raise CleanupError(f"Table {table.name} has no primary key; inspect it before cleanup.")
    if len(columns) == 1:
        return columns[0].in_([key[0] for key in keys])
    return tuple_(*columns).in_(list(keys))


def _keys(conn, table, condition):
    columns = _identity(table)
    if not columns:
        if conn.execute(select(func.count()).select_from(table).where(condition)).scalar_one():
            raise CleanupError(f"Related table {table.name} has no primary key; inspect it before cleanup.")
        return set()
    return {tuple(row) for row in conn.execute(select(*columns).where(condition)).all()}


def _is_actor(constraint):
    elements = list(constraint.elements)
    return (len(elements) == 1 and elements[0].column.table.name == "staff"
            and (elements[0].parent.table.name, elements[0].parent.name) in _ACTOR_COLUMNS)


def _related_keys(conn, child, constraint, selected):
    elements = list(constraint.elements)
    parent = elements[0].column.table
    if not selected[parent.name]:
        return set()
    # EXISTS handles self references without alias/name collisions and returns
    # only identities. Auth tokens, password hashes, and student PII are unused.
    if parent is child:
        alias = parent.alias("cleanup_parent")
        join = and_(*(element.parent == alias.c[element.column.name] for element in elements))
        parent_condition = _where(alias, selected[parent.name])
        condition = select(1).select_from(alias).where(join, parent_condition).exists()
    else:
        join = and_(*(element.parent == element.column for element in elements))
        condition = select(1).select_from(parent).where(join, _where(parent, selected[parent.name])).exists()
    return _keys(conn, child, condition)


def _check_owned_rows(conn, table, keys, school_ids, staff_keys):
    if not keys:
        return
    if "school_id" in table.c:
        bad = conn.execute(select(func.count()).select_from(table).where(
            _where(table, keys), table.c.school_id.is_not(None), ~table.c.school_id.in_(school_ids))).scalar_one()
        if bad:
            raise CleanupError(f"Cross-school dependency in {table.name}; no cleanup was applied. Inspect the affected relationship.")
    # Device-linked sessions must never pull in an unrelated account's session.
    if table.name in _AUTH_TABLES and "staff_id" in table.c:
        owners = [key[0] for key in staff_keys]
        bad = conn.execute(select(func.count()).select_from(table).where(
            _where(table, keys), or_(table.c.staff_id.is_(None), ~table.c.staff_id.in_(owners)))).scalar_one()
        if bad:
            raise CleanupError(f"Cross-account dependency in {table.name}; no cleanup was applied.")
    if "school_id" not in table.c:
        # Also protect unfamiliar legacy auth/dependency tables. For example,
        # a row referencing a selected session but another person's staff ID
        # must not be swept in merely through its session foreign key.
        for constraint in table.foreign_key_constraints:
            elements = list(constraint.elements)
            if elements[0].column.table.name != "staff" or _is_actor(constraint):
                continue
            nonnull = and_(*(element.parent.is_not(None) for element in elements))
            has_owner = _keys(conn, table, and_(_where(table, keys), nonnull))
            allowed = _related_keys(conn, table, constraint, defaultdict(set, staff=staff_keys))
            if has_owner - allowed:
                raise CleanupError(f"Cross-account dependency in {table.name}; no cleanup was applied.")


def _delete_order(tables, selected):
    names = {name for name, keys in selected.items() if keys}
    children = defaultdict(set)
    for name in names:
        for constraint in tables[name].foreign_key_constraints:
            parent = next(iter(constraint.elements)).column.table.name
            if parent in names and not _is_actor(constraint):
                children[parent].add(name)
    visiting, visited, order = set(), set(), []

    def visit(name):
        if name in visiting:
            raise CleanupError("A circular database dependency needs manual review; no cleanup was applied.")
        if name in visited:
            return
        visiting.add(name)
        for child in sorted(children[name]):
            visit(child)
        visiting.remove(name)
        visited.add(name)
        order.append(name)

    for name in sorted(names):
        visit(name)
    return order


def _plan(conn, tables, school_ids):
    schools = [row for row in _school_list(conn, tables) if row["school_id"] in school_ids]
    if len(schools) != len(school_ids):
        missing = sorted(set(school_ids) - {row["school_id"] for row in schools})
        raise CleanupError(f"School IDs not found: {','.join(map(str, missing))}. No cleanup was applied.")
    selected = defaultdict(set)
    for name, table in tables.items():
        if "school_id" in table.c:
            selected[name] = _keys(conn, table, table.c.school_id.in_(school_ids))
    staff = tables["staff"]
    staff_keys = selected["staff"]
    if staff_keys and conn.execute(select(func.count()).select_from(staff).where(
            _where(staff, staff_keys), staff.c.role == "super_admin")).scalar_one():
        raise CleanupError("A selected school contains a super administrator. That account and school are protected.")
    # Follow every reflected FK, including legacy tables missing from the ORM.
    # School ownership and account ownership are checked before accepting each
    # extra row, so database CASCADE cannot silently reach another tenant.
    changed = True
    while changed:
        changed = False
        for name, table in tables.items():
            for constraint in table.foreign_key_constraints:
                if _is_actor(constraint):
                    continue
                extra = _related_keys(conn, table, constraint, selected) - selected[name]
                if not extra:
                    continue
                _check_owned_rows(conn, table, extra, school_ids, staff_keys)
                if name in {"schools", "staff"}:
                    raise CleanupError(f"Unexpected dependency involving {name}; no cleanup was applied.")
                selected[name].update(extra)
                changed = True
    # An event has two subject-ownership columns. A malformed event owned by a
    # selected school but targeting somebody at a retained school must not lose
    # that retained person's decision history just because school_id matched.
    events = tables.get("application_events")
    if events is not None and "staff_id" in events.c and selected[events.name]:
        owners = [key[0] for key in staff_keys]
        foreign_subject = conn.execute(select(func.count()).select_from(events).where(
            _where(events, selected[events.name]), events.c.staff_id.is_not(None),
            ~events.c.staff_id.in_(owners))).scalar_one()
        if foreign_subject:
            raise CleanupError("Cross-school application history needs review; no cleanup was applied.")
    # Retained audit/application history may lose only its actor attribution.
    # A different school's student/grade attribution is an ownership anomaly
    # requiring review, rather than permission to modify that tenant's record.
    actor_updates = []
    for name, table in tables.items():
        for constraint in table.foreign_key_constraints:
            if not _is_actor(constraint):
                continue
            external = _related_keys(conn, table, constraint, selected) - selected[name]
            if external:
                if name not in {"application_events", "audit_logs"}:
                    _check_owned_rows(conn, table, external, school_ids, staff_keys)
                column = next(iter(constraint.elements)).parent
                if not column.nullable:
                    raise CleanupError(f"A retained {name} actor reference cannot be cleared; inspect it before cleanup.")
                actor_updates.append((name, column.name, external))
    order = _delete_order(tables, selected)
    accounts = []
    if staff_keys:
        accounts = [dict(id=str(row.id), school_id=row.school_id, email=row.email, role=row.role)
                    for row in conn.execute(select(staff.c.id, staff.c.school_id, staff.c.email, staff.c.role)
                                            .where(_where(staff, staff_keys)).order_by(staff.c.email)).mappings()]
    summary = {
        "school_ids": school_ids, "schools": schools,
        "table_counts": {name: len(selected[name]) for name in sorted(selected) if selected[name]},
        "accounts": accounts,
        "actor_reference_updates": [{"table": name, "column": column, "count": len(keys)}
                                    for name, column, keys in actor_updates],
        "applied": False,
    }
    return summary, selected, actor_updates, order


def cleanup_connection(conn, school_ids, *, apply=False, confirm_school_ids=None):
    """Inspect or atomically delete selected school IDs on a sync Connection.

    Apply owns its transaction and requires no pre-existing transaction. Commit
    fixture setup, or roll back a preceding read-only inspection, before apply.
    Any error rolls the apply transaction back. No student details are returned.
    """
    school_ids = _ids(school_ids)
    expected = ",".join(map(str, school_ids))
    if apply and confirm_school_ids != expected:
        raise CleanupError(f"Apply requires --confirm-school-ids {expected}, matching exactly the selected IDs.")
    if not apply:
        return _plan(conn, _reflect(conn)[1], school_ids)[0]
    if conn.in_transaction():
        raise CleanupError("Finish the current transaction before applying cleanup. No changes were applied.")
    try:
        if conn.dialect.name == "sqlite":
            # Session setting only: no schema/data writes. Enable before BEGIN
            # because SQLite ignores a change made inside a real transaction.
            conn.exec_driver_sql("PRAGMA foreign_keys=ON")
            enabled = conn.exec_driver_sql("PRAGMA foreign_keys").scalar_one()
            conn.commit()
            if enabled != 1:
                raise CleanupError("SQLite foreign-key enforcement could not be enabled; no cleanup was applied.")
            conn.exec_driver_sql("BEGIN IMMEDIATE")
        elif conn.dialect.name == "postgresql":
            conn.begin()
            conn.exec_driver_sql("SET LOCAL lock_timeout = '15s'")
        else:
            raise CleanupError("Only SQLite and PostgreSQL are supported.")
        metadata, tables = _reflect(conn)
        if conn.dialect.name == "postgresql":
            # Reflected table names are quoted by the dialect; no user-supplied
            # identifier or credential is interpolated into the SQL.
            preparer = conn.dialect.identifier_preparer
            for table in sorted(metadata.tables.values(), key=lambda item: item.fullname):
                conn.exec_driver_sql(f"LOCK TABLE {preparer.format_table(table)} IN SHARE ROW EXCLUSIVE MODE")
        summary, selected, actor_updates, order = _plan(conn, tables, school_ids)
        for name, column, keys in actor_updates:
            conn.execute(update(tables[name]).where(_where(tables[name], keys)).values({column: None}))
        # Clear selected actor references before ANY deletion, avoiding ordering
        # constraints between otherwise unrelated tenant tables and accounts.
        for name, keys in selected.items():
            if not keys:
                continue
            table = tables[name]
            values = {column: None for table_name, column in _ACTOR_COLUMNS
                      if table_name == name and column in table.c and table.c[column].nullable}
            if values:
                conn.execute(update(table).where(_where(table, keys)).values(values))
        for name in order:
            table = tables[name]
            conn.execute(delete(table).where(_where(table, selected[name])))
        # Fail closed if a trigger unexpectedly retained any selected row.
        for name, keys in selected.items():
            if keys and conn.execute(select(func.count()).select_from(tables[name])
                                     .where(_where(tables[name], keys))).scalar_one():
                raise CleanupError("Some selected records remain; the entire cleanup was rolled back.")
        conn.commit()
        summary["applied"] = True
        return summary
    except Exception:
        conn.rollback()
        raise


def _print_schools(schools):
    for row in schools:
        principals = ", ".join(row["principal_emails"]) or "(none)"
        print(f"ID {row['school_id']}: {row['name']} | {row['city']} | {row['status']}")
        print(f"  Principal email(s): {principals}; staff: {row['staff_count']}; students: {row['student_count']}")


async def _run(arguments):
    from app.core.config import Settings
    settings = Settings()
    engine = create_async_engine(settings.sqlalchemy_url, connect_args=settings.db_connect_args)
    try:
        if engine.dialect.name == "sqlite" and engine.url.database not in (None, "", ":memory:"):
            if not Path(engine.url.database).is_file():
                raise CleanupError("SQLite database does not exist. Check DATABASE_URL; no file was created.")
        async with engine.connect() as connection:
            if arguments.list or not arguments.school_id:
                schools = await connection.run_sync(list_schools)
                _print_schools(schools)
                print("Read-only list. Select exact school IDs to inspect a cleanup; nothing was deleted.")
                return
            summary = await connection.run_sync(lambda conn: cleanup_connection(
                conn, arguments.school_id, apply=arguments.apply, confirm_school_ids=arguments.confirm_school_ids))
        _print_schools(summary["schools"])
        print("Linked records selected for permanent deletion:")
        for name, count in summary["table_counts"].items():
            print(f"  {name}: {count}")
        print("Accounts selected for deletion (emails become available after apply):")
        for account in summary["accounts"]:
            print(f"  {account['email']} ({account['role']}, school {account['school_id']})")
        for item in summary["actor_reference_updates"]:
            print(f"  Retained {item['table']}: clear {item['column']} attribution on {item['count']} record(s).")
        if summary["applied"]:
            print("Applied. Only the selected schools and linked records were permanently removed.")
            print("School names and removed account emails can be registered again. Other schools and super administrators were preserved.")
        else:
            confirmation = ",".join(map(str, summary["school_ids"]))
            print("Inspection only. Verify the selected schools, take a verified backup, and stop the API before applying.")
            print(f"Confirmation required for apply: --confirm-school-ids {confirmation}")
    finally:
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="Read-only school identity and count list.")
    parser.add_argument("--school-id", action="append", type=int, help="Exact school ID; repeat for each selected school.")
    parser.add_argument("--apply", action="store_true", help="Permanently delete the explicitly selected schools.")
    parser.add_argument("--confirm-school-ids", help="Exact sorted comma-separated school IDs, required for apply.")
    arguments = parser.parse_args()
    if arguments.list and (arguments.school_id or arguments.apply or arguments.confirm_school_ids):
        parser.error("--list cannot be combined with cleanup selection or apply options.")
    if arguments.apply and not arguments.school_id:
        parser.error("--apply requires one or more explicit --school-id values.")
    if arguments.confirm_school_ids and not arguments.apply:
        parser.error("--confirm-school-ids is used only with --apply.")
    try:
        asyncio.run(_run(arguments))
    except (CleanupError, SQLAlchemyError, ValueError, OSError) as exc:
        # SQLAlchemy/driver/Settings errors can include connection URLs, SQL, or
        # tokens. Never print their raw exception, traceback, or representations.
        reason = str(exc) if isinstance(exc, CleanupError) else "database operation failed. Check --list before retrying."
        print(f"Cleanup stopped: {reason}")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
