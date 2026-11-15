"""Separate section advice from subject teaching without deleting old rows.

Stop the API and take a database backup before applying:
    python migrate_section_advisers.py          # inspection only
    python migrate_section_advisers.py --apply  # create table + safe backfill

No model initialization, seeding, assignment deletion, or grade writes occur.
Legacy blank-subject assignments remain for deliberate principal review.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from pathlib import Path

from sqlalchemy import Column, DateTime, ForeignKey, Integer, MetaData, String, Table, UniqueConstraint, func, inspect, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import create_async_engine


class MigrationError(RuntimeError):
    pass


BACKFILL_KEY = "section_advisers_v1"


def _tables(conn):
    inspector = inspect(conn)
    metadata = MetaData()
    for name in ("schools", "staff", "sections", "teacher_assignments"):
        if not inspector.has_table(name):
            raise MigrationError(f"Missing {name} table. Check the selected database.")
        Table(name, metadata, autoload_with=conn)
    if inspector.has_table("section_advisers"):
        adviser = Table("section_advisers", metadata, autoload_with=conn)
        if not {"adviser_id", "school_id", "section_id", "teacher_id"}.issubset(adviser.c.keys()):
            raise MigrationError("Unexpected section_advisers definition; inspect it before applying.")
        unique = inspector.get_unique_constraints("section_advisers") + [
            item for item in inspector.get_indexes("section_advisers") if item.get("unique")
        ]
        if not any(item.get("column_names") == ["school_id", "section_id"] for item in unique):
            raise MigrationError("section_advisers is missing its one-adviser-per-section constraint.")
    else:
        adviser = Table("section_advisers", metadata,
            Column("adviser_id", Integer, primary_key=True, index=True),
            Column("school_id", Integer, ForeignKey("schools.school_id", ondelete="CASCADE"), nullable=False, index=True),
            Column("section_id", Integer, ForeignKey("sections.section_id", ondelete="CASCADE"), nullable=False, index=True),
            Column("teacher_id", metadata.tables["staff"].c.id.type,
                   ForeignKey("staff.id", ondelete="CASCADE"), nullable=False, index=True),
            Column("created_at", DateTime(timezone=True), server_default=func.now()),
            Column("updated_at", DateTime(timezone=True), server_default=func.now()),
            UniqueConstraint("school_id", "section_id", name="uq_section_adviser_per_school"),
        )
    if inspector.has_table("trace_schema_migrations"):
        marker = Table("trace_schema_migrations", metadata, autoload_with=conn)
        if not {"migration_name", "applied_at"}.issubset(marker.c.keys()):
            raise MigrationError("Unexpected migration marker definition; inspect it before applying.")
    else:
        marker = Table("trace_schema_migrations", metadata,
            Column("migration_name", String(100), primary_key=True),
            Column("applied_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
        )
    return metadata, adviser, marker


def _plan(conn):
    metadata, adviser, marker = _tables(conn)
    assignments = metadata.tables["teacher_assignments"]
    staff, sections = metadata.tables["staff"], metadata.tables["sections"]
    if not {"school_id", "section_id", "teacher_id", "is_adviser", "subject_id"}.issubset(assignments.c.keys()):
        raise MigrationError("Teacher assignments do not have the expected multi-school structure.")
    people = {str(row.id): row for row in conn.execute(select(staff)).mappings()}
    section_schools = dict(conn.execute(select(sections.c.section_id, sections.c.school_id)).all())
    has_table = inspect(conn).has_table("section_advisers")
    marker_exists = inspect(conn).has_table(marker.name)
    completed = bool(conn.execute(select(marker.c.migration_name).where(
        marker.c.migration_name == BACKFILL_KEY)).first()) if marker_exists else False
    if completed and not has_table:
        raise MigrationError("Adviser backfill was completed but its table is missing; restore or inspect the database.")
    existing = {(row.school_id, row.section_id): str(row.teacher_id)
                for row in conn.execute(select(adviser)).mappings()} if has_table else {}
    grouped = defaultdict(set)
    invalid = []
    for row in conn.execute(select(assignments).where(assignments.c.is_adviser.is_(True))).mappings():
        teacher = people.get(str(row.teacher_id))
        if teacher is None or teacher.school_id != row.school_id or teacher.role != "teacher" or section_schools.get(row.section_id) != row.school_id:
            invalid.append(row.assignment_id)
            continue
        grouped[(row.school_id, row.section_id)].add(str(row.teacher_id))
    copy, conflicts, preserved, skipped = [], [], [], []
    for (school_id, section_id), teachers in sorted(grouped.items()):
        key = (school_id, section_id)
        if key in existing:
            preserved.append({"school_id": school_id, "section_id": section_id,
                              "differs_from_legacy": teachers != {existing[key]}})
        elif completed:
            # Advice may have been deliberately removed after deployment.
            # Old flags must never resurrect that responsibility on reruns.
            skipped.append({"school_id": school_id, "section_id": section_id})
        elif len(teachers) > 1:
            conflicts.append({"school_id": school_id, "section_id": section_id,
                              "teacher_ids": sorted(teachers)})
        else:
            teacher_key = next(iter(teachers))
            # Reuse the driver's native ID type (UUID on PostgreSQL, text on
            # SQLite), instead of coercing reflected UUID columns to strings.
            copy.append({"school_id": school_id, "section_id": section_id,
                         "teacher_id": people[teacher_key].id})
    return {"create_table": not has_table, "copy": copy, "conflicts": conflicts,
            "invalid_assignment_ids": invalid, "preserved": preserved,
            "backfill_completed": completed, "skipped_legacy_sections": skipped,
            "create_marker_table": not marker_exists,
            "legacy_subject_review_count": conn.execute(select(func.count()).select_from(assignments)
                .where(assignments.c.subject_id.is_(None))).scalar_one()}, adviser, marker


def migrate_connection(conn, *, apply=False):
    if conn.dialect.name not in {"sqlite", "postgresql"}:
        raise MigrationError("Only SQLite and PostgreSQL are supported.")
    if not apply:
        return _plan(conn)[0]
    if conn.in_transaction():
        conn.rollback()
    if conn.dialect.name == "sqlite":
        conn.exec_driver_sql("BEGIN IMMEDIATE")
    else:
        conn.begin()
        conn.exec_driver_sql("SET LOCAL lock_timeout = '15s'")
        conn.exec_driver_sql("LOCK TABLE teacher_assignments IN SHARE MODE")
    try:
        plan, adviser, marker = _plan(conn)
        if not plan["backfill_completed"] and (plan["conflicts"] or plan["invalid_assignment_ids"]):
            raise MigrationError("Conflicting or invalid legacy advisers need review; no changes were applied.")
        if plan["create_table"]:
            adviser.create(conn)
        if plan["copy"]:
            conn.execute(adviser.insert(), plan["copy"])
        if plan["create_marker_table"]:
            marker.create(conn)
        if not plan["backfill_completed"]:
            conn.execute(marker.insert().values(migration_name=BACKFILL_KEY))
        conn.commit()
        return plan
    except Exception:
        conn.rollback()
        raise


async def _run(apply):
    from app.core.config import Settings
    settings = Settings()
    engine = create_async_engine(settings.sqlalchemy_url, connect_args=settings.db_connect_args)
    try:
        if engine.dialect.name == "sqlite" and engine.url.database not in (None, "", ":memory:"):
            if not Path(engine.url.database).is_file():
                raise MigrationError("SQLite database does not exist. Check DATABASE_URL; no file was created.")
        async with engine.connect() as connection:
            plan = await connection.run_sync(lambda conn: migrate_connection(conn, apply=apply))
        print(f"Section adviser table: {'create' if plan['create_table'] else 'already present'}")
        print(f"Legacy advisers to copy: {len(plan['copy'])}; existing advisers preserved: {len(plan['preserved'])}")
        print(f"Blank-subject assignments requiring principal review: {plan['legacy_subject_review_count']}")
        if plan["backfill_completed"]:
            print("Backfill was already completed. Removed advisers will not be restored from old flags.")
        for conflict in plan["conflicts"]:
            print(f"CONFLICT: school {conflict['school_id']}, section {conflict['section_id']}: multiple legacy advisers.")
        if plan["invalid_assignment_ids"]:
            print(f"INVALID legacy assignment IDs: {plan['invalid_assignment_ids']}")
        for item in plan["preserved"]:
            if item["differs_from_legacy"]:
                print(f"REVIEW: school {item['school_id']}, section {item['section_id']}: existing adviser retained over legacy flags.")
        print("Applied. Legacy assignment rows and grades were preserved." if apply else
              "Inspection only. Back up the database and stop the API before running with --apply.")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    arguments = parser.parse_args()
    try:
        asyncio.run(_run(arguments.apply))
    except (MigrationError, SQLAlchemyError) as exc:
        # Driver exceptions may contain URLs/credentials; show safe database
        # errors only, never the raw SQLAlchemy exception.
        print(f"Migration stopped: {exc if isinstance(exc, MigrationError) else 'database operation failed; no changes were applied.'}")
        raise SystemExit(1) from None
