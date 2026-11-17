"""Preserve existing records while separating academic setup by school year.

Inspection is the default. Stop the API and take a verified backup before:
    python migrate_school_year_scope.py --legacy-year 2026-2027 --apply

Use --school-year SCHOOL_ID:YEAR to override a school's legacy assignment year.
Class-record headers are retained for every year that already has matching
scores. Empty future years never receive those headers. No grades, enrolments,
student profiles, current-year flags, or existing assignments are deleted.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from pathlib import Path

from sqlalchemy import (
    Column, DateTime, ForeignKey, Index, Integer, MetaData, String, Table,
    UniqueConstraint, func, inspect, select,
)
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import create_async_engine


class MigrationError(RuntimeError):
    pass


MARKER = "school_year_scope_v1"
SPECS = {
    "teacher_assignments": {
        "pk": "assignment_id", "old": ("teacher_id", "section_id", "subject_id"),
        "new": ("school_year_id", "teacher_id", "section_id", "subject_id"),
        "unique": "uq_teacher_year_section_subject",
    },
    "section_advisers": {
        "pk": "adviser_id", "old": ("school_id", "section_id"),
        "new": ("school_id", "school_year_id", "section_id"),
        "unique": "uq_section_adviser_per_year",
    },
    "class_record_setups": {
        "pk": "setup_id", "old": ("section_id", "subject_id", "grading_period"),
        "new": ("school_year_id", "section_id", "subject_id", "grading_period"),
        "unique": "uq_class_record_setup_per_year",
    },
}
REQUIRED = ("schools", "school_years", "staff", "sections", "subjects",
            "student_enrollments", "student_paces", *SPECS)


def _quote(conn, name):
    return conn.dialect.identifier_preparer.quote(name)


def _metadata(conn):
    inspector = inspect(conn)
    missing = [name for name in REQUIRED if not inspector.has_table(name)]
    if missing:
        raise MigrationError("Missing required tables: " + ", ".join(missing)
                             + ". Finish earlier migrations before continuing.")
    metadata = MetaData()
    metadata.reflect(conn, only=list(REQUIRED))
    return metadata


def _plan(conn, legacy_year=None, school_years=None):
    metadata = _metadata(conn)
    inspector = inspect(conn)
    overrides = school_years or {}
    years = {r.school_year_id: dict(r) for r in conn.execute(
        select(metadata.tables["school_years"])).mappings()}
    schools = {r.school_id: r.name for r in conn.execute(
        select(metadata.tables["schools"])).mappings()}
    named_years = {(r["school_id"], r["year"]): key for key, r in years.items()}
    current = defaultdict(list)
    for key, row in years.items():
        if row["is_current"]:
            current[row["school_id"]].append(key)
    errors, confirmations, chosen = [], set(), {}

    def choose(school_id):
        if school_id in chosen:
            return chosen[school_id]
        label = overrides.get(school_id, legacy_year)
        if label is None:
            confirmations.add(school_id)
            options = current[school_id]
            target = options[0] if len(options) == 1 else None
        else:
            target = named_years.get((school_id, label))
        if target is None:
            errors.append(f"School {school_id} needs an existing legacy school year"
                          + (f" named {label}." if label else "."))
        chosen[school_id] = target
        return target

    for school_id, label in overrides.items():
        if school_id not in schools or (school_id, label) not in named_years:
            errors.append(f"School-year override {school_id}:{label} does not exist.")
    sections = {r.section_id: r.school_id for r in conn.execute(
        select(metadata.tables["sections"])).mappings()}
    subjects = {r.subject_id: dict(r) for r in conn.execute(
        select(metadata.tables["subjects"])).mappings()}
    people = {str(r.id): r.school_id for r in conn.execute(
        select(metadata.tables["staff"])).mappings()}
    enrollments = {r.enrollment_id: dict(r) for r in conn.execute(
        select(metadata.tables["student_enrollments"])).mappings()}
    matched = defaultdict(set)
    for pace in conn.execute(select(metadata.tables["student_paces"])).mappings():
        enrollment = enrollments.get(pace.enrollment_id)
        if enrollment is None or enrollment["school_id"] != pace.school_id:
            errors.append(f"Score {pace.pace_id} has an invalid enrolment reference.")
            continue
        year = years.get(enrollment["school_year_id"])
        if year is None or year["school_id"] != pace.school_id:
            errors.append(f"Score {pace.pace_id} has an invalid school-year reference.")
            continue
        subject_id = pace.get("subject_id")
        if subject_id is None:
            # Older score rows used a subject name rather than a foreign key.
            candidates = [key for key, subject in subjects.items()
                          if subject["school_id"] == pace.school_id
                          and subject["grade_level_id"] == enrollment["grade_level_id"]
                          and subject["subject_name"] == pace.subject]
            subject_id = candidates[0] if len(candidates) == 1 else None
        if subject_id is not None:
            matched[(pace.school_id, enrollment["section_id"], subject_id,
                     pace.grading_period)].add(enrollment["school_year_id"])

    changes, counts, old_rows = {}, {}, {}
    for name, spec in SPECS.items():
        table = metadata.tables[name]
        rows = [dict(row) for row in conn.execute(select(table)).mappings()]
        old_rows[name] = rows
        changes[name] = []
        seen = set()
        for row in rows:
            school_id, identity = row["school_id"], row[spec["pk"]]
            if sections.get(row["section_id"]) != school_id:
                errors.append(f"{name} row {identity} has a section from another school.")
            if name in ("teacher_assignments", "section_advisers"):
                if people.get(str(row["teacher_id"])) != school_id:
                    errors.append(f"{name} row {identity} has a teacher from another school.")
            if name != "section_advisers" and row.get("subject_id") is not None:
                if subjects.get(row["subject_id"], {}).get("school_id") != school_id:
                    errors.append(f"{name} row {identity} has a subject from another school.")
            existing = row.get("school_year_id")
            if existing is not None:
                if years.get(existing, {}).get("school_id") != school_id:
                    errors.append(f"{name} row {identity} has a year from another school.")
                targets = [existing]
            elif name == "class_record_setups":
                targets = sorted(matched.get((school_id, row["section_id"],
                                             row["subject_id"], row["grading_period"]), set()))
                if not targets:
                    targets = [choose(school_id)]
            else:
                targets = [choose(school_id)]
            for target in targets:
                values = dict(row, school_year_id=target)
                key = tuple(values.get(col) for col in spec["new"])
                # PostgreSQL and SQLite allow multiple NULL-subject legacy
                # teaching rows. Preserve that existing review state.
                if None not in key:
                    if key in seen:
                        errors.append(f"Conflicting {name} records would share a school-year key.")
                    seen.add(key)
            if existing is None:
                changes[name].append({"id": identity, "school_id": school_id, "years": targets})
        columns = {c["name"]: c for c in inspector.get_columns(name)}
        uniques = inspector.get_unique_constraints(name) + [
            item for item in inspector.get_indexes(name) if item.get("unique")]
        year_fks = [fk for fk in inspector.get_foreign_keys(name)
                    if fk.get("constrained_columns") == ["school_year_id"]]
        for fk in year_fks:
            if fk.get("referred_table") != "school_years" or fk.get("referred_columns") != ["school_year_id"]:
                errors.append(f"{name} has an unexpected school-year foreign key.")
        counts[name] = {
            "rows": len(rows), "unscoped": len(changes[name]),
            "add_year_column": "school_year_id" not in columns,
            "nullable": columns.get("school_year_id", {}).get("nullable", True),
            "header_copies": sum(max(0, len(c["years"])-1) for c in changes[name]),
            "year_unique": any(tuple(u.get("column_names") or []) == spec["new"] for u in uniques),
            "old_unique": any(set(u.get("column_names") or []) == set(spec["old"]) for u in uniques),
            "year_foreign_key": any(fk.get("referred_table") == "school_years"
                                    and fk.get("referred_columns") == ["school_year_id"]
                                    and fk.get("options", {}).get("ondelete", "NO ACTION").upper() in ("RESTRICT", "NO ACTION")
                                    for fk in year_fks),
        }

    marker_present = inspector.has_table("trace_schema_migrations")
    completed = False
    if marker_present:
        marker = Table("trace_schema_migrations", MetaData(), autoload_with=conn)
        completed = conn.execute(select(marker.c.migration_name).where(
            marker.c.migration_name == MARKER)).first() is not None
    if completed and any(c["unscoped"] or c["add_year_column"] or c["nullable"]
                         or not c["year_unique"] or c["old_unique"] or not c["year_foreign_key"]
                         for c in counts.values()):
        errors.append("The migration was marked complete but its year columns, data or constraints have changed.")
    mappings = [{"school_id": key, "school": schools.get(key, ""),
                 "year_id": year_id, "year": years.get(year_id, {}).get("year"),
                 "explicit": key not in confirmations} for key, year_id in sorted(chosen.items())]
    return {
        "tables": counts, "assignments": changes, "legacy_years": mappings,
        "confirmation_required_for_schools": sorted(confirmations),
        "errors": sorted(set(errors)), "already_applied": completed,
    }, metadata, old_rows


def _drop_old_uniques(conn, name, spec):
    inspector = inspect(conn)
    q = lambda value: _quote(conn, value)
    old = set(spec["old"])
    for constraint in inspector.get_unique_constraints(name):
        if set(constraint.get("column_names") or []) == old:
            conn.exec_driver_sql(f"ALTER TABLE {q(name)} DROP CONSTRAINT {q(constraint['name'])}")
    for index in inspect(conn).get_indexes(name):
        if index.get("unique") and not index.get("duplicates_constraint") and set(index.get("column_names") or []) == old:
            conn.exec_driver_sql(f"DROP INDEX {q(index['name'])}")


def _rebuild_sqlite(conn, name, spec):
    inspector = inspect(conn)
    for other in inspector.get_table_names():
        for fk in inspector.get_foreign_keys(other):
            if fk.get("referred_table") == name:
                raise MigrationError(f"Unexpected incoming reference {other} -> {name}; no changes applied.")
    metadata = MetaData()
    original = Table(name, metadata, autoload_with=conn)
    index_specs = inspector.get_indexes(name)
    triggers = conn.exec_driver_sql(
        "SELECT sql FROM sqlite_master WHERE type='trigger' AND tbl_name=? AND sql IS NOT NULL ORDER BY name",
        (name,),
    ).scalars().all()
    fresh_metadata = MetaData()
    for related in metadata.tables.values():
        if related.name != name:
            related.to_metadata(fresh_metadata)
    temporary = "_trace_year_" + name
    if inspector.has_table(temporary):
        raise MigrationError(f"Unexpected temporary table {temporary}; inspect before continuing.")
    replacement = original.to_metadata(fresh_metadata, name=temporary)
    replacement.indexes.clear()
    for constraint in list(replacement.constraints):
        if isinstance(constraint, UniqueConstraint) and set(constraint.columns.keys()) == set(spec["old"]):
            replacement.constraints.remove(constraint)
    if not any(isinstance(c, UniqueConstraint) and tuple(c.columns.keys()) == spec["new"]
               for c in replacement.constraints):
        replacement.append_constraint(UniqueConstraint(*spec["new"], name=spec["unique"]))
    replacement.c.school_year_id.nullable = False
    if "school_years" not in fresh_metadata.tables:
        Table("school_years", fresh_metadata, autoload_with=conn)
    if not replacement.c.school_year_id.foreign_keys:
        replacement.c.school_year_id.append_foreign_key(
            ForeignKey("school_years.school_year_id", ondelete="RESTRICT"))
    else:
        for fk in replacement.c.school_year_id.foreign_keys:
            fk.ondelete = "RESTRICT"
            fk.constraint.ondelete = "RESTRICT"
    replacement.create(conn)
    column_names = list(original.c.keys())
    conn.execute(replacement.insert().from_select(column_names, select(*[original.c[c] for c in column_names])))
    original.drop(conn)
    q = lambda value: _quote(conn, value)
    conn.exec_driver_sql(f"ALTER TABLE {q(temporary)} RENAME TO {q(name)}")
    final = Table(name, MetaData(), autoload_with=conn)
    for item in index_specs:
        columns = item.get("column_names") or []
        if None in columns:
            raise MigrationError(f"Expression index on {name} needs explicit review.")
        if item.get("unique") and set(columns) == set(spec["old"]):
            continue
        Index(item["name"], *[final.c[c] for c in columns], unique=item.get("unique", False)).create(conn)
    for sql in triggers:
        conn.exec_driver_sql(sql)


def _finish_postgres(conn, name, spec):
    inspector = inspect(conn)
    q = lambda value: _quote(conn, value)
    fk = [f for f in inspector.get_foreign_keys(name) if f.get("constrained_columns") == ["school_year_id"]]
    if fk and (fk[0].get("referred_table") != "school_years" or fk[0].get("referred_columns") != ["school_year_id"]):
        raise MigrationError(f"Unexpected school-year foreign key on {name}.")
    if fk and fk[0].get("options", {}).get("ondelete", "NO ACTION").upper() not in ("RESTRICT", "NO ACTION"):
        conn.exec_driver_sql(f"ALTER TABLE {q(name)} DROP CONSTRAINT {q(fk[0]['name'])}")
        fk = []
    if not fk:
        conn.exec_driver_sql(f"ALTER TABLE {q(name)} ADD CONSTRAINT {q('fk_' + name + '_school_year')} "
                             f"FOREIGN KEY (school_year_id) REFERENCES school_years(school_year_id) ON DELETE RESTRICT")
    conn.exec_driver_sql(f"ALTER TABLE {q(name)} ALTER COLUMN school_year_id SET NOT NULL")
    uniques = inspector.get_unique_constraints(name) + [i for i in inspector.get_indexes(name) if i.get("unique")]
    if not any(tuple(i.get("column_names") or []) == spec["new"] for i in uniques):
        columns = ", ".join(q(c) for c in spec["new"])
        conn.exec_driver_sql(f"ALTER TABLE {q(name)} ADD CONSTRAINT {q(spec['unique'])} UNIQUE ({columns})")


def migrate_connection(conn, *, apply=False, legacy_year=None, school_years=None):
    if conn.dialect.name not in ("sqlite", "postgresql"):
        raise MigrationError("Only SQLite and PostgreSQL are supported.")
    if not apply:
        return _plan(conn, legacy_year, school_years)[0]
    if conn.in_transaction():
        conn.rollback()
    if conn.dialect.name == "sqlite":
        conn.exec_driver_sql("BEGIN IMMEDIATE")
    else:
        conn.begin()
        conn.exec_driver_sql("SET LOCAL lock_timeout = '15s'")
        conn.exec_driver_sql("LOCK TABLE " + ", ".join(REQUIRED) + " IN ACCESS EXCLUSIVE MODE")
    try:
        plan, metadata, old_rows = _plan(conn, legacy_year, school_years)
        if plan["errors"]:
            raise MigrationError(" ".join(plan["errors"]))
        if plan["confirmation_required_for_schools"]:
            raise MigrationError("Confirm the legacy assignment year with --legacy-year YEAR or "
                                 "--school-year SCHOOL_ID:YEAR. Inspection made no changes.")
        if plan["already_applied"]:
            conn.rollback()
            return plan
        q = lambda value: _quote(conn, value)
        for name in SPECS:
            if plan["tables"][name]["add_year_column"]:
                sql = f"ALTER TABLE {q(name)} ADD COLUMN school_year_id INTEGER"
                if conn.dialect.name == "sqlite":
                    sql += " REFERENCES school_years(school_year_id) ON DELETE RESTRICT"
                conn.exec_driver_sql(sql)
        current = MetaData()
        current.reflect(conn, only=list(REQUIRED))
        for name, spec in SPECS.items():
            if conn.dialect.name == "postgresql":
                _drop_old_uniques(conn, name, spec)
            table = current.tables[name]
            for change in plan["assignments"][name]:
                conn.execute(table.update().where(table.c[spec["pk"]] == change["id"])
                             .values(school_year_id=change["years"][0]))
        if conn.dialect.name == "sqlite":
            for name, spec in SPECS.items():
                _rebuild_sqlite(conn, name, spec)
            current = MetaData()
            current.reflect(conn, only=list(REQUIRED))
        table = current.tables["class_record_setups"]
        originals = {r["setup_id"]: r for r in old_rows["class_record_setups"]}
        for change in plan["assignments"]["class_record_setups"]:
            original = originals[change["id"]]
            for year_id in change["years"][1:]:
                copy = {key: value for key, value in original.items() if key != "setup_id"}
                copy["school_year_id"] = year_id
                conn.execute(table.insert().values(**copy))
        if conn.dialect.name == "postgresql":
            for name, spec in SPECS.items():
                _finish_postgres(conn, name, spec)
        for name in SPECS:
            indexes = inspect(conn).get_indexes(name)
            if not any(i.get("column_names") == ["school_year_id"] for i in indexes):
                t = Table(name, MetaData(), autoload_with=conn)
                Index("ix_" + name + "_school_year_id", t.c.school_year_id).create(conn)
        marker_meta = MetaData()
        marker = Table("trace_schema_migrations", marker_meta,
                       Column("migration_name", String(100), primary_key=True),
                       Column("applied_at", DateTime(timezone=True), server_default=func.now(), nullable=False))
        marker.create(conn, checkfirst=True)
        conn.execute(marker.insert().values(migration_name=MARKER))
        if conn.dialect.name == "sqlite" and conn.exec_driver_sql("PRAGMA foreign_key_check").first():
            raise MigrationError("Foreign-key validation failed; no changes were applied.")
        conn.commit()
        return plan
    except Exception:
        conn.rollback()
        raise


async def _run(args):
    from app.core.config import Settings
    settings = Settings()
    engine = create_async_engine(settings.sqlalchemy_url, connect_args=settings.db_connect_args)
    overrides = {}
    for value in args.school_year:
        try:
            key, year = value.split(":", 1)
            key = int(key)
            if key < 1 or not year or key in overrides:
                raise ValueError
        except ValueError:
            raise MigrationError("Use each --school-year override once as SCHOOL_ID:YEAR.") from None
        overrides[key] = year
    try:
        if engine.dialect.name == "sqlite" and engine.url.database not in (None, "", ":memory:"):
            if not Path(engine.url.database).is_file():
                raise MigrationError("SQLite database does not exist. Check DATABASE_URL; no file was created.")
        async with engine.connect() as connection:
            plan = await connection.run_sync(lambda c: migrate_connection(
                c, apply=args.apply, legacy_year=args.legacy_year, school_years=overrides))
        for name, info in plan["tables"].items():
            print(f"{name}: {info['rows']} rows; attach {info['unscoped']} legacy rows to a year"
                  + (f"; preserve headers for {info['header_copies']} additional existing score years" if info["header_copies"] else ""))
        for mapping in plan["legacy_years"]:
            label = "confirmed" if mapping["explicit"] else "proposal only; confirm before apply"
            print(f"School {mapping['school_id']} ({mapping['school']}): {mapping['year']} [{label}]")
        for error in plan["errors"]:
            print("REVIEW: " + error)
        if plan["already_applied"]:
            print("Already applied. No records were copied again or reassigned.")
        else:
            print("Applied. Existing scores, grades, enrolments and student profiles were preserved."
                  if args.apply else "Inspection only. Back up the database and stop the API before using --apply.")
        if plan["errors"]:
            return 1
        return 0
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--legacy-year", help="Existing year for legacy assignments and headers without scores")
    parser.add_argument("--school-year", action="append", default=[], metavar="SCHOOL_ID:YEAR")
    args = parser.parse_args()
    try:
        raise SystemExit(asyncio.run(_run(args)))
    except (MigrationError, SQLAlchemyError) as exc:
        print("Migration stopped: " + (str(exc) if isinstance(exc, MigrationError)
                                      else "database operation failed; no changes were applied."))
        raise SystemExit(1) from None
