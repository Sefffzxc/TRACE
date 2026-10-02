"""Move an existing single-school database to the multi-school schema.

    python migrate_multitenant.py --school-name "Lord's Blessing Christian Academy" \
                                  --city "Cagayan de Oro" --private --tracks-units PACE

What it does, in one transaction per step:

1. Creates the ``schools`` table (and any other table that does not exist yet).
2. Adds the ``school_id`` column to every table that now needs one.
3. Creates one school from the arguments, already approved.
4. Puts every existing row into that school.
5. Promotes the existing administrator to that school's administrator.

It is safe to run twice: every step checks first and skips what is already
done. It does not drop anything, and it does not delete a single row — the
alternative to running this is `python seed.py --reset`, which throws the
database away, and that is only reasonable while the data is still demo data.

Why hand-written rather than Alembic: this project has no migration history to
build on, and one reviewable file that a reader can check line by line is worth
more here than a framework. If the schema keeps moving after this, adopt
Alembic properly rather than adding a second script like this one.
"""
from __future__ import annotations

import argparse
import asyncio
from typing import List, Tuple

from sqlalchemy import text

from app.core.config import settings
from app.db.session import async_engine, init_models

# Every table that holds one school's work, and whether a row can exist without
# a school (only `staff` can — the system operator belongs to none).
TENANT_TABLES: List[Tuple[str, bool]] = [
    ("staff", True),
    ("school_years", False),
    ("grade_levels", False),
    ("sections", False),
    ("subjects", False),
    ("teacher_assignments", False),
    ("class_schedules", False),
    ("teacher_availabilities", False),
    ("students", False),
    ("student_enrollments", False),
    ("student_paces", False),
    ("early_warnings", False),
    ("ai_risk_predictions", False),
    ("data_quality_logs", False),
]


async def table_exists(conn, table: str) -> bool:
    if settings.is_sqlite:
        row = await conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name=:t"),
            {"t": table},
        )
    else:
        row = await conn.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = current_schema() AND table_name = :t"
            ),
            {"t": table},
        )
    return row.first() is not None


async def column_exists(conn, table: str, column: str) -> bool:
    if settings.is_sqlite:
        rows = await conn.execute(text(f"PRAGMA table_info({table})"))
        return any(r[1] == column for r in rows)
    rows = await conn.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = current_schema() "
            "AND table_name = :t AND column_name = :c"
        ),
        {"t": table, "c": column},
    )
    return rows.first() is not None


async def migrate(args) -> None:
    print(f"Database: {'SQLite' if settings.is_sqlite else 'PostgreSQL'}")
    print()

    # ── 1. Create anything that does not exist yet, including `schools` ────
    print("1. Creating missing tables…")
    await init_models()
    print("   done.")

    async with async_engine.begin() as conn:
        # ── 2. Add the school_id columns ──────────────────────────────────
        print("\n2. Adding school_id columns…")
        added = skipped = missing = 0
        for table, _nullable in TENANT_TABLES:
            if not await table_exists(conn, table):
                print(f"   {table:24} table not present — skipped")
                missing += 1
                continue
            if await column_exists(conn, table, "school_id"):
                print(f"   {table:24} already has school_id")
                skipped += 1
                continue
            # Added nullable regardless of the model's NOT NULL, because the
            # existing rows have no value yet. Step 4 fills them in; tightening
            # to NOT NULL afterwards would need a table rebuild on SQLite and
            # buys nothing here, so the constraint lives in the ORM.
            await conn.execute(
                text(f"ALTER TABLE {table} ADD COLUMN school_id INTEGER")
            )
            print(f"   {table:24} school_id added")
            added += 1
        print(f"   {added} added, {skipped} already present, {missing} absent.")

        # ── 3. The school itself ──────────────────────────────────────────
        print("\n3. Creating the school…")
        existing = (
            await conn.execute(
                text("SELECT school_id, name FROM schools ORDER BY school_id LIMIT 1")
            )
        ).first()

        if existing is not None:
            school_id, name = existing[0], existing[1]
            print(f"   school #{school_id} already exists: {name} — reusing it.")
        else:
            tracks = bool(args.tracks_units)
            unit = (args.tracks_units or "Module").strip()
            plural = unit if unit.endswith("s") else unit + "s"
            await conn.execute(
                text(
                    "INSERT INTO schools ("
                    " name, short_name, school_type, address, city, province, region,"
                    " contact_number, contact_email,"
                    " tracks_units, unit_label_singular, unit_label_plural,"
                    " default_units_per_subject,"
                    " passing_mark, grading_periods, grading_period_label,"
                    " status"
                    ") VALUES ("
                    " :name, :short_name, :school_type, :address, :city, :province, :region,"
                    " :contact_number, :contact_email,"
                    " :tracks_units, :unit_s, :unit_p, :units_per_subject,"
                    " :passing_mark, :periods, :period_label,"
                    " 'approved')"
                ),
                {
                    "name": args.school_name,
                    "short_name": args.short_name,
                    "school_type": "private" if args.private else "public",
                    "address": args.address or "",
                    "city": args.city,
                    "province": args.province,
                    "region": args.region,
                    "contact_number": args.contact_number,
                    "contact_email": args.contact_email,
                    "tracks_units": tracks,
                    "unit_s": unit,
                    "unit_p": plural,
                    "units_per_subject": args.units_per_subject,
                    "passing_mark": args.passing_mark,
                    "periods": args.grading_periods,
                    "period_label": args.grading_period_label,
                },
            )
            school_id = (
                await conn.execute(
                    text("SELECT school_id FROM schools ORDER BY school_id DESC LIMIT 1")
                )
            ).scalar_one()
            print(f"   created school #{school_id}: {args.school_name} ({args.city})")
            print(f"   tracks lesson units: {tracks}" + (f" (called {unit})" if tracks else ""))

        # ── 4. Move every existing row into it ────────────────────────────
        print("\n4. Assigning existing rows to that school…")
        total = 0
        for table, _nullable in TENANT_TABLES:
            if not await table_exists(conn, table):
                continue
            result = await conn.execute(
                text(f"UPDATE {table} SET school_id = :sid WHERE school_id IS NULL"),
                {"sid": school_id},
            )
            count = result.rowcount or 0
            total += count
            print(f"   {table:24} {count:6} row(s)")
        print(f"   {total} rows assigned.")

        # ── 5. The existing administrator becomes this school's ───────────
        print("\n5. Checking accounts…")
        admins = (
            await conn.execute(
                text("SELECT email FROM staff WHERE role = 'admin' ORDER BY created_at")
            )
        ).all()
        supers = (
            await conn.execute(
                text("SELECT email FROM staff WHERE role = 'super_admin'")
            )
        ).all()
        for row in admins:
            print(f"   administrator of {args.school_name}: {row[0]}")
        if supers:
            for row in supers:
                print(f"   system operator: {row[0]}")
            # The operator belongs to no school — undo step 4 for them.
            await conn.execute(
                text("UPDATE staff SET school_id = NULL WHERE role = 'super_admin'")
            )
            print("   (operator detached from the school, as it should be)")
        else:
            print("   no system operator yet — create one with:")
            print("     python seed.py --create-superadmin you@example.com")

    await async_engine.dispose()
    print("\nMigration complete.")
    print("Nothing was deleted. Sign in as before; everything now belongs to")
    print(f"school #{school_id}, and new schools can register alongside it.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Move a single-school database to the multi-school schema."
    )
    parser.add_argument("--school-name", required=True)
    parser.add_argument("--city", required=True)
    parser.add_argument("--short-name", default=None)
    parser.add_argument("--address", default="")
    parser.add_argument("--province", default=None)
    parser.add_argument("--region", default=None)
    parser.add_argument("--contact-number", default=None)
    parser.add_argument("--contact-email", default=None)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--private", action="store_true", default=True)
    group.add_argument("--public", dest="private", action="store_false")
    parser.add_argument(
        "--tracks-units",
        default=None,
        metavar="LABEL",
        help="What this school calls its lesson units, e.g. PACE. "
             "Omit entirely if the school does not track them.",
    )
    parser.add_argument("--units-per-subject", type=int, default=12)
    parser.add_argument("--passing-mark", type=float, default=75.0)
    parser.add_argument("--grading-periods", type=int, default=4)
    parser.add_argument("--grading-period-label", default="Quarter")
    asyncio.run(migrate(parser.parse_args()))


if __name__ == "__main__":
    main()
