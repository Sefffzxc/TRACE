"""Populate the database with a realistic demo school.

    python seed.py            # create tables and seed if empty
    python seed.py --reset    # drop everything first

Accounts created (password for all of them: Lbca@2026):
    admin@lbca.edu.ph          administrator
    mramos@lbca.edu.ph         teacher (adviser, Grade 7 - Faith)
    jsantos@lbca.edu.ph        teacher (adviser, Grade 8 - Hope)
    alim@lbca.edu.ph           teacher (adviser, Grade 9 - Love)
    pending@lbca.edu.ph        teacher, still awaiting approval
"""
from __future__ import annotations

import argparse
import asyncio
import random
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select

from app.core.security import get_password_hash
from app.db.session import AsyncSessionLocal, Base, async_engine, init_models
from app.models.academic import (
    ClassSchedule,
    GradeLevel,
    SchoolYear,
    Section,
    Subject,
    TeacherAssignment,
)
from app.models.staff import (
    ROLE_ADMIN,
    ROLE_TEACHER,
    STATUS_APPROVED,
    STATUS_PENDING,
    Staff,
)
from app.models.students import EarlyWarning, Student, StudentEnrollment, StudentPace
from app.services import risk

PASSWORD = "Lbca@2026"
random.seed(20260920)

FIRST_NAMES_M = [
    "Josiah", "Caleb", "Nathaniel", "Elijah", "Gabriel", "Samuel", "Isaiah",
    "Ezekiel", "Matthias", "Lucas", "Andrei", "Rafael", "Miguel", "Paolo",
    "Joshua", "Daniel", "Ezra", "Simon", "Tobias", "Emmanuel",
]
FIRST_NAMES_F = [
    "Abigail", "Hannah", "Naomi", "Rebekah", "Esther", "Miriam", "Leah",
    "Rachel", "Deborah", "Priscilla", "Sofia", "Isabella", "Althea", "Bianca",
    "Yvonne", "Clarisse", "Danielle", "Elena", "Faith", "Grace",
]
LAST_NAMES = [
    "Reyes", "Santos", "Cruz", "Bautista", "Ocampo", "Garcia", "Mendoza",
    "Torres", "Domingo", "Villanueva", "Aquino", "Castillo", "Navarro",
    "Del Rosario", "Fernandez", "Pascual", "Gonzales", "Ramirez", "Salazar",
    "Tolentino",
]
STREETS = [
    "Mabini St.", "Rizal Ave.", "Bonifacio Drive", "Del Pilar St.",
    "Luna Street", "Aguinaldo Road", "San Juan St.", "Katipunan Ave.",
]
BARANGAYS = ["Bagong Silang", "San Isidro", "Sta. Cruz", "Poblacion", "Maligaya"]

SUBJECT_PLAN = [
    ("Mathematics", "MATH", 12),
    ("English", "ENG", 12),
    ("Science", "SCI", 12),
    ("Social Studies", "SOC", 10),
    ("Word Building", "WRB", 10),
    ("Bible Reading", "BIB", 8),
]

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
PERIODS = [("08:00", "09:00"), ("09:15", "10:15"), ("10:30", "11:30"),
           ("13:00", "14:00"), ("14:15", "15:15")]


def assessment_record(profile: str) -> dict:
    """Invent a DepEd class record that matches a demo student's profile.

    These are the nine inputs the risk model reads. They are made up — this is
    a demo school with invented students, not LBCA data — but they are made up
    *coherently*: a struggling learner gets absences, low percentage scores and
    unsubmitted items together, so the model's output on the demo is
    interpretable rather than random.

    One row in eight is left partly blank on purpose, so the "scored with
    partial data" state that a real half-finished quarter produces is visible
    in the demo instead of only in production.
    """
    if profile == "strong":
        absences = random.randint(0, 1)
        ww = round(random.uniform(90, 99), 1)
        pt = round(random.uniform(90, 100), 1)
        missed_ww, missed_pt = 0, 0
    elif profile == "steady":
        absences = random.randint(0, 2)
        ww = round(random.uniform(80, 92), 1)
        pt = round(random.uniform(82, 94), 1)
        missed_ww, missed_pt = random.randint(0, 1), 0
    elif profile == "slipping":
        absences = random.randint(2, 4)
        ww = round(random.uniform(70, 83), 1)
        pt = round(random.uniform(70, 85), 1)
        missed_ww, missed_pt = random.randint(1, 3), random.randint(0, 2)
    else:  # struggling
        absences = random.randint(4, 9)
        ww = round(random.uniform(48, 72), 1)
        pt = round(random.uniform(45, 70), 1)
        missed_ww, missed_pt = random.randint(3, 6), random.randint(2, 5)

    record = {
        "absences": absences,
        "ww_percent": ww,
        "ww_total_raw": round(ww * 0.6, 1),   # a 60-point Written Work total
        "pt_percent": pt,
        "pt_total_raw": round(pt * 0.5, 1),   # a 50-point Performance Task total
        "missed_ww_items": missed_ww,
        "missed_pt_items": missed_pt,
        "extracurricular_count": random.randint(0, 3),
    }

    if random.random() < 0.125:
        for key in ("pt_percent", "pt_total_raw", "missed_pt_items"):
            record[key] = None

    return record


def _now() -> datetime:
    return datetime.now(timezone.utc)


def make_staff(email, first, last, role, contact, department, approved=True, employee_no=None):
    return Staff(
        email=email,
        password_hash=get_password_hash(PASSWORD),
        first_name=first,
        last_name=last,
        contact_number=contact,
        role=role,
        employee_no=employee_no,
        department=department,
        account_status=STATUS_APPROVED if approved else STATUS_PENDING,
        is_approved=approved,
        is_active=True,
        approved_at=_now() if approved else None,
    )


async def seed() -> None:
    async with AsyncSessionLocal() as db:
        existing = (await db.execute(select(func.count()).select_from(Staff))).scalar_one()
        if existing:
            print(f"Database already has {existing} staff account(s) — nothing to do.")
            print("Run `python seed.py --reset` to rebuild the demo data.")
            return

        # ── Staff ────────────────────────────────────────────────────────────
        admin = make_staff(
            "admin@lbca.edu.ph", "Grace", "Mercado", ROLE_ADMIN,
            "0917 555 0100", "School Administration", employee_no="EMP-0001",
        )
        teachers = [
            make_staff("mramos@lbca.edu.ph", "Maria", "Ramos", ROLE_TEACHER,
                       "0917 555 0111", "Junior High School", employee_no="EMP-0002"),
            make_staff("jsantos@lbca.edu.ph", "Jonathan", "Santos", ROLE_TEACHER,
                       "0917 555 0122", "Junior High School", employee_no="EMP-0003"),
            make_staff("alim@lbca.edu.ph", "Angeline", "Lim", ROLE_TEACHER,
                       "0917 555 0133", "Junior High School", employee_no="EMP-0004"),
            make_staff("rcruz@lbca.edu.ph", "Ruben", "Cruz", ROLE_TEACHER,
                       "0917 555 0144", "Values Education", employee_no="EMP-0005"),
        ]
        pending = make_staff(
            "pending@lbca.edu.ph", "Katrina", "Velasco", ROLE_TEACHER,
            "0917 555 0155", "Junior High School", approved=False,
        )
        db.add_all([admin, *teachers, pending])
        await db.flush()

        # ── School year ──────────────────────────────────────────────────────
        year = SchoolYear(
            year="2026-2027",
            start_date=date(2026, 6, 1),
            end_date=date(2027, 3, 31),
            is_current=True,
        )
        previous = SchoolYear(
            year="2025-2026",
            start_date=date(2025, 6, 2),
            end_date=date(2026, 3, 31),
            is_current=False,
        )
        db.add_all([year, previous])
        await db.flush()

        # ── Grade levels, sections, subjects ─────────────────────────────────
        levels = []
        for index, number in enumerate([7, 8, 9, 10], start=1):
            levels.append(
                GradeLevel(level=str(number), name=f"Grade {number}", sort_order=index)
            )
        db.add_all(levels)
        await db.flush()

        section_names = {
            7: ["Faith", "Charity"],
            8: ["Hope", "Patience"],
            9: ["Love", "Kindness"],
            10: ["Joy"],
        }
        sections = []
        for level in levels:
            for name in section_names[int(level.level)]:
                sections.append(
                    Section(
                        grade_level_id=level.grade_level_id,
                        section_code=f"G{level.level}-{name[:3].upper()}",
                        name=f"Grade {level.level} - {name}",
                        room=f"Room {level.level}0{len(sections) % 3 + 1}",
                        capacity=40,
                    )
                )
        db.add_all(sections)
        await db.flush()

        subjects = []
        for level in levels:
            for name, code, total in SUBJECT_PLAN:
                subjects.append(
                    Subject(
                        grade_level_id=level.grade_level_id,
                        subject_name=name,
                        subject_code=f"{code}{level.level}",
                        total_paces=total,
                        is_active=True,
                    )
                )
        db.add_all(subjects)
        await db.flush()

        # ── Teacher assignments (one adviser per section) ────────────────────
        assignments = []
        for index, section in enumerate(sections):
            adviser = teachers[index % len(teachers)]
            assignments.append(
                TeacherAssignment(
                    teacher_id=adviser.id,
                    section_id=section.section_id,
                    is_adviser=True,
                )
            )
        db.add_all(assignments)
        await db.flush()

        # ── Timetable for the first three sections ───────────────────────────
        schedules = []
        for section in sections[:3]:
            level_subjects = [
                s for s in subjects if s.grade_level_id == section.grade_level_id
            ]
            adviser = next(a for a in assignments if a.section_id == section.section_id)
            for day_index, day in enumerate(DAYS):
                for period_index, (start, end) in enumerate(PERIODS[:4]):
                    subject = level_subjects[
                        (day_index + period_index) % len(level_subjects)
                    ]
                    schedules.append(
                        ClassSchedule(
                            school_year_id=year.school_year_id,
                            section_id=section.section_id,
                            subject_id=subject.subject_id,
                            teacher_id=adviser.teacher_id,
                            day_of_week=day,
                            start_time=start,
                            end_time=end,
                            room=section.room or "TBA",
                        )
                    )
        db.add_all(schedules)

        # ── Students, enrolments, PACE records ───────────────────────────────
        # Roughly 14 weeks into a 40-week year, so ~4 of 12 booklets expected.
        weeks_elapsed = 14
        total_weeks = 36

        adviser_names = {
            a.section_id: next(t for t in teachers if t.id == a.teacher_id).full_name
            for a in assignments
        }

        student_count = 0
        for section in sections:
            level = next(
                l for l in levels if l.grade_level_id == section.grade_level_id
            )
            level_subjects = [
                s for s in subjects if s.grade_level_id == section.grade_level_id
            ]
            roster_size = random.randint(14, 20)

            for _ in range(roster_size):
                gender = random.choice(["Male", "Female"])
                first = random.choice(
                    FIRST_NAMES_M if gender == "Male" else FIRST_NAMES_F
                )
                last = random.choice(LAST_NAMES)
                guardian_first = random.choice(FIRST_NAMES_M + FIRST_NAMES_F)
                age = 12 + int(level.level) - 7

                student = Student(
                    first_name=first,
                    last_name=last,
                    gender=gender,
                    birth_date=date(2026 - age, random.randint(1, 12), random.randint(1, 28)),
                    address=f"{random.randint(1, 240)} {random.choice(STREETS)}, "
                            f"Brgy. {random.choice(BARANGAYS)}",
                    guardian_first_name=guardian_first,
                    guardian_last_name=last,
                    guardian_contact=f"09{random.randint(10, 99)} {random.randint(100, 999)} {random.randint(1000, 9999)}",
                    guardian_relationship=random.choice(["Parent", "Parent", "Guardian"]),
                    created_by=admin.id,
                )
                db.add(student)
                await db.flush()
                student.login_id = f"S{student.student_id:04d}"
                student_count += 1

                enrollment = StudentEnrollment(
                    student_id=student.student_id,
                    grade_level_id=section.grade_level_id,
                    section_id=section.section_id,
                    school_year_id=year.school_year_id,
                    enrolled_by=admin.id,
                    enrollment_date=date(2026, 6, 3),
                    is_active=True,
                )
                db.add(enrollment)
                await db.flush()

                # A few learners are deliberately struggling so the early-warning
                # screens have something real to show.
                profile = random.choices(
                    ["strong", "steady", "slipping", "struggling"],
                    weights=[25, 45, 20, 10],
                )[0]

                for subject in level_subjects:
                    expected = max(
                        1, round(subject.total_paces * weeks_elapsed / total_weeks)
                    )
                    if profile == "strong":
                        current = min(subject.total_paces, expected + random.randint(0, 2))
                        attendance = round(random.uniform(96, 100), 1)
                    elif profile == "steady":
                        current = max(0, expected - random.randint(0, 1))
                        attendance = round(random.uniform(92, 99), 1)
                    elif profile == "slipping":
                        current = max(0, expected - random.randint(1, 3))
                        attendance = round(random.uniform(84, 94), 1)
                    else:
                        current = max(0, expected - random.randint(3, 5))
                        attendance = round(random.uniform(70, 86), 1)

                    evaluation = risk.evaluate(
                        current, expected, subject.total_paces, attendance
                    )
                    pace = StudentPace(
                        student_id=student.student_id,
                        enrollment_id=enrollment.enrollment_id,
                        subject_id=subject.subject_id,
                        subject=subject.subject_name,
                        current_pace=current,
                        expected_pace=expected,
                        total_paces=subject.total_paces,
                        pace_percent=evaluation["pace_percent"],
                        paces_behind=evaluation["paces_behind"],
                        attendance=attendance,
                        average_score=round(random.uniform(74, 97), 1),
                        recorded_by=adviser_names and admin.id,
                        **assessment_record(profile),
                    )
                    db.add(pace)

                    if evaluation["risk_level"] != "low":
                        db.add(
                            EarlyWarning(
                                student_id=student.student_id,
                                enrollment_id=enrollment.enrollment_id,
                                subject=subject.subject_name,
                                teacher=adviser_names.get(section.section_id, "Unassigned"),
                                risk_level=evaluation["risk_level"],
                                risk_score=evaluation["risk_score"],
                                paces_behind=evaluation["paces_behind"],
                                pace_percent=evaluation["pace_percent"],
                                attendance=attendance,
                                status=evaluation["status"],
                                trend=random.choice(
                                    ["declining", "stable", "stable", "improving"]
                                ),
                                last_activity=random.choice(
                                    ["Today", "Yesterday", "2 days ago", "This week"]
                                ),
                                intervention=evaluation["intervention"],
                            )
                        )

        await db.commit()

        warnings = (
            await db.execute(select(func.count()).select_from(EarlyWarning))
        ).scalar_one()

        print("Demo data created.")
        print(f"  staff      : {len(teachers) + 2}")
        print(f"  sections   : {len(sections)}")
        print(f"  subjects   : {len(subjects)}")
        print(f"  students   : {student_count}")
        print(f"  warnings   : {warnings}")
        print()
        print("Sign in with:")
        print(f"  admin@lbca.edu.ph    / {PASSWORD}   (administrator)")
        print(f"  mramos@lbca.edu.ph   / {PASSWORD}   (teacher)")
        print()
        print("First sign-in on a new browser asks for a 6-digit code — it is")
        print("printed in this server's console and, while DEBUG_OTP is on, shown")
        print("on the verification screen itself.")


async def score() -> None:
    """Ask the real AI service to score every seeded record.

    The predictions are genuine model output; only the inputs are invented. No
    prediction is ever written by hand here — if the service cannot be reached
    the rows are marked ``source="fallback"`` exactly as they would be in
    production, and this prints how many, so a demo is never quietly showing
    rule-based numbers under an AI label.
    """
    from app.models.students import AIRiskPrediction
    from app.services import ml_client

    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(StudentPace))).scalars().all()
        if not rows:
            print("Nothing to score — seed the database first.")
            return

        levels = {
            level.grade_level_id: level.name
            for level in (await db.execute(select(GradeLevel))).scalars()
        }
        enrollments = {
            e.enrollment_id: e
            for e in (await db.execute(select(StudentEnrollment))).scalars()
        }

        pairs = [
            (
                row,
                levels.get(
                    getattr(enrollments.get(row.enrollment_id), "grade_level_id", None)
                ),
            )
            for row in rows
        ]

        fallback, versions = 0, set()
        batch = 500
        for start in range(0, len(pairs), batch):
            part = pairs[start:start + batch]
            results = await ml_client.predict_many(part)
            for (row, _label), result in zip(part, results):
                if result.source == "fallback":
                    fallback += 1
                else:
                    versions.add(result.model_version)
                db.add(
                    AIRiskPrediction(
                        student_id=row.student_id,
                        enrollment_id=row.enrollment_id,
                        pace_id=row.pace_id,
                        subject=row.subject,
                        **result.as_columns(),
                    )
                )
        await db.commit()

        print(f"Scored {len(pairs)} PACE record(s).")
        if fallback:
            print(
                f"  WARNING: {fallback} of them fell back to the PACE rules because the "
                f"AI service was unreachable at {ml_client.settings.AI_SERVICE_URL}."
            )
            print("  They are labelled 'rule-based' in the UI. Start the AI service and")
            print("  press 'Re-score records' on the AI Risk Model page to replace them.")
        else:
            print(f"  All scored by the model: {', '.join(sorted(versions))}")


async def reset() -> None:
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    print("All tables dropped.")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Seed the LBCA demo database.")
    parser.add_argument("--reset", action="store_true", help="drop all tables first")
    parser.add_argument(
        "--score",
        action="store_true",
        help="after seeding, ask the AI service to score every record",
    )
    parser.add_argument(
        "--score-only",
        action="store_true",
        help="skip seeding and only re-score what is already in the database",
    )
    args = parser.parse_args()

    if args.score_only:
        await score()
        await async_engine.dispose()
        return

    if args.reset:
        import app.models  # noqa: F401

        await reset()

    await init_models()
    await seed()
    if args.score:
        await score()
    await async_engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
