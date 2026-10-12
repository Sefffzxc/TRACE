"""Populate the database with two realistic demo schools.

    python seed.py                              # create tables and seed if empty
    python seed.py --reset                      # drop everything first
    python seed.py --create-superadmin EMAIL    # only add a system operator

Two schools, not one, on purpose: a demo with a single school proves nothing
about multi-tenancy. The second school is a public DepEd school that does not
track PACE booklets, so the pair also demonstrates that `tracks_units` — not
`school_type` — is what decides what a teacher sees.

Accounts created (password for all of them: Lbca@2026):

    superadmin@example.com    system operator, belongs to no school

    Lord's Blessing Christian Academy (private, tracks PACEs)
    admin@lbca.edu.ph          principal
    mramos@lbca.edu.ph         teacher (adviser, Grade 7 - Faith)
    jsantos@lbca.edu.ph        teacher (adviser, Grade 8 - Hope)
    alim@lbca.edu.ph           teacher (adviser, Grade 9 - Love)
    rcruz@lbca.edu.ph          teacher
    pending@lbca.edu.ph        teacher, still awaiting approval

    Mabini National High School (public, no unit tracking)
    principal@mabini.edu.ph    principal
    tdelacruz@mabini.edu.ph    teacher (adviser, Grade 7 - Narra)
    mbaluyot@mabini.edu.ph     teacher (adviser, Grade 8 - Molave)
"""
from __future__ import annotations

import argparse
import asyncio
import random
import secrets
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
from app.models.schools import (
    SCHOOL_APPROVED,
    TYPE_PRIVATE,
    TYPE_PUBLIC,
    School,
)
from app.models.staff import (
    ROLE_ADMIN,
    ROLE_SUPER_ADMIN,
    ROLE_TEACHER,
    STATUS_APPROVED,
    STATUS_PENDING,
    Staff,
)
from app.models.students import EarlyWarning, Student, StudentEnrollment, StudentPace
from app.services import risk

PASSWORD = "Lbca@2026"
# The demo's system operator.
#
# `example.com` rather than something like `system.local`: `.local` is reserved
# for multicast DNS, and the address validation the API applies at sign-in
# refuses reserved domains outright. An account created at such an address can
# be written to the database and then never signed into — which is exactly what
# happened here before this was changed. `example.com` is reserved by RFC 2606
# for documentation, so it can never belong to anyone, and it passes validation.
SUPERADMIN_EMAIL = "superadmin@example.com"
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

# The second school follows the DepEd curriculum, so its subject list is the
# DepEd one. The unit totals are still filled in because a StudentPace row also
# carries the class record the risk model reads — see `build_school`.
DEPED_SUBJECT_PLAN = [
    ("Mathematics", "MATH", 12),
    ("English", "ENG", 12),
    ("Science", "SCI", 12),
    ("Filipino", "FIL", 10),
    ("Araling Panlipunan", "AP", 10),
]

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
PERIODS = [("08:00", "09:00"), ("09:15", "10:15"), ("10:30", "11:30"),
           ("13:00", "14:00"), ("14:15", "15:15")]


# ══════════════════════════════════════════════════════════════════════════════
# The two schools, as data
# ══════════════════════════════════════════════════════════════════════════════
# Kept as plain dictionaries rather than two copies of the seeding code, so the
# schools differ only in the ways a reader cares about — curriculum, unit
# tracking, size, accounts — and so a third school is a dict rather than a
# branch. Everything here is invented demo data.
SCHOOL_PLANS = [
    {
        "school": {
            "name": "Lord's Blessing Christian Academy",
            "short_name": "LBCA",
            "school_type": TYPE_PRIVATE,
            "address": "Purok 3, Zone 5, Gusa",
            "city": "Cagayan de Oro",
            "province": "Misamis Oriental",
            "region": "Region X (Northern Mindanao)",
            "contact_number": "(088) 856 1024",
            "contact_email": "office@lbca.edu.ph",
            # A School of Tomorrow school: progress is counted in PACE
            # booklets, so the unit screens are the ones its teachers live in.
            "tracks_units": True,
            "unit_label_singular": "PACE",
            "unit_label_plural": "PACEs",
            "default_units_per_subject": 12,
            "passing_mark": 75.0,
        },
        "admin": {
            "email": "admin@lbca.edu.ph", "first": "Grace", "last": "Mercado",
            "contact": "0917 555 0100", "department": "School Administration",
        },
        "teachers": [
            {"email": "mramos@lbca.edu.ph", "first": "Maria", "last": "Ramos",
             "contact": "0917 555 0111", "department": "Junior High School"},
            {"email": "jsantos@lbca.edu.ph", "first": "Jonathan", "last": "Santos",
             "contact": "0917 555 0122", "department": "Junior High School"},
            {"email": "alim@lbca.edu.ph", "first": "Angeline", "last": "Lim",
             "contact": "0917 555 0133", "department": "Junior High School"},
            {"email": "rcruz@lbca.edu.ph", "first": "Ruben", "last": "Cruz",
             "contact": "0917 555 0144", "department": "Values Education"},
        ],
        # One account left unapproved so the principal's approval queue is not
        # empty in the demo.
        "pending": {
            "email": "pending@lbca.edu.ph", "first": "Katrina", "last": "Velasco",
            "contact": "0917 555 0155", "department": "Junior High School",
        },
        "levels": [7, 8, 9, 10],
        "sections": {
            7: ["Faith", "Charity"],
            8: ["Hope", "Patience"],
            9: ["Love", "Kindness"],
            10: ["Joy"],
        },
        "subjects": SUBJECT_PLAN,
        "roster": (14, 20),
        "timetable_sections": 3,
    },
    {
        "school": {
            "name": "Mabini National High School",
            "short_name": "MNHS",
            "school_type": TYPE_PUBLIC,
            "address": "Mabini Extension, Barangay Carmen",
            "city": "Cagayan de Oro",
            "province": "Misamis Oriental",
            "region": "Region X (Northern Mindanao)",
            "contact_number": "(088) 858 7731",
            "contact_email": "mabini.nhs@deped.gov.ph",
            # No booklets here. The unit labels are deliberately left at their
            # model defaults: an unused label that says "Module" is honest,
            # whereas copying "PACE" across would put a word on the screen that
            # this school has never used.
            "tracks_units": False,
            "passing_mark": 75.0,
        },
        "admin": {
            "email": "principal@mabini.edu.ph", "first": "Rodolfo", "last": "Bagamasbad",
            "contact": "0918 442 0190", "department": "Office of the Principal",
        },
        "teachers": [
            {"email": "tdelacruz@mabini.edu.ph", "first": "Teresita", "last": "Dela Cruz",
             "contact": "0918 442 0191", "department": "Junior High School"},
            {"email": "mbaluyot@mabini.edu.ph", "first": "Melchor", "last": "Baluyot",
             "contact": "0918 442 0192", "department": "Junior High School"},
        ],
        # Deliberately about half of the first school: the point of the second
        # tenant is that the scoping holds, not that it is large, and a seed
        # that takes twice as long to run is a seed people stop running.
        "levels": [7, 8, 9],
        "sections": {
            7: ["Narra"],
            8: ["Molave"],
            9: ["Acacia"],
        },
        "subjects": DEPED_SUBJECT_PLAN,
        "roster": (10, 14),
        "timetable_sections": 2,
    },
]


def assessment_record(profile: str) -> dict:
    """Invent a DepEd class record that matches a demo student's profile.

    These are the nine inputs the risk model reads. They are made up — these
    are demo schools with invented students, not real school data — but they
    are made up *coherently*: a struggling learner gets absences, low
    percentage scores and unsubmitted items together, so the model's output on
    the demo is interpretable rather than random.

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


def make_staff(school_id, email, first, last, role, contact, department,
               approved=True, employee_no=None):
    """One school's staff account.

    ``school_id`` is first and required because it is the field that decides
    what this person can ever see — forgetting it is the one mistake in this
    script that would quietly break tenant isolation, so it cannot be omitted.
    """
    return Staff(
        school_id=school_id,
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


def check_signinable(email: str) -> str:
    """Refuse an address the API's own sign-in validation would reject.

    The API validates the email field on sign-in with pydantic's EmailStr,
    which rejects reserved domains such as `.local`, `.test` and `.invalid`.
    Nothing stops this script writing such an address straight into the
    database, so without this check the script can cheerfully create an account
    that is impossible to sign in to — and the failure surfaces much later, as
    a validation error on the login screen that looks like a bug in the form.

    Better to fail here, where the cause is obvious.
    """
    def refuse(reason: str):
        raise SystemExit(
            f"\n'{email}' cannot be used as a sign-in address.\n"
            f"  {reason}\n"
            "  The sign-in form applies the same rule, so this account would be\n"
            "  created and then be impossible to sign in to. Use a real domain,\n"
            "  or example.com for a demo account.\n"
        )

    # Checked directly, not only through email-validator, so the guard still
    # works when this script runs somewhere that package is not installed —
    # which is precisely when a silent bad account would go unnoticed.
    # RFC 2606 and RFC 6761 reserve these; no mail is deliverable to any of them.
    reserved = ("local", "localhost", "test", "invalid", "example", "internal",
                "onion", "arpa")
    domain = email.rpartition("@")[2].lower().strip(".")
    if not domain or "." not in domain and domain not in ("",):
        if not domain:
            refuse("There is no domain after the @ sign.")
    tld = domain.rpartition(".")[2] if "." in domain else domain
    if tld in reserved or domain in reserved:
        refuse(f"'.{tld}' is a reserved domain and cannot receive mail.")

    try:
        from email_validator import EmailNotValidError, validate_email
    except ImportError:  # pragma: no cover
        return email
    try:
        validate_email(email, check_deliverability=False)
    except EmailNotValidError as exc:
        refuse(str(exc))
    return email


def make_super_admin(email: str, password: str) -> Staff:
    """The operator of the deployment, who belongs to no school.

    ``school_id`` stays NULL, and that is the whole safety story: every
    student-data query is filtered by the caller's school, so for this account
    there is no school to filter by and nothing to return. It is not a
    permission flag someone can forget to check — see ROLE_SUPER_ADMIN in
    app/models/staff.py and `school_scope` in app/api/deps.py.
    """
    return Staff(
        school_id=None,
        email=check_signinable(email),
        password_hash=get_password_hash(password),
        first_name="System",
        last_name="Administrator",
        contact_number="0917 555 0000",
        role=ROLE_SUPER_ADMIN,
        department="Platform Operations",
        account_status=STATUS_APPROVED,
        is_approved=True,
        is_active=True,
        approved_at=_now(),
    )


async def build_school(db, plan: dict, approved_by) -> dict:
    """Create one school and a full year of its work.

    Called once per school. Every row written here takes its ``school_id`` from
    the school created at the top — nothing is derived from "the only school in
    the database", because after this script runs there is no such thing.

    Returns a small summary for the printed report at the end.
    """
    school = School(
        status=SCHOOL_APPROVED,
        approved_at=_now(),
        # Recording which operator approved the school makes the approval chain
        # visible in the demo data rather than only in the docstrings.
        approved_by=approved_by,
        **plan["school"],
    )
    db.add(school)
    # Flushed on its own first: every insert below needs the generated id.
    await db.flush()
    school_id = school.school_id

    # ── Staff ────────────────────────────────────────────────────────────────
    # Employee numbers restart at EMP-0001 for each school, which the schema
    # allows (they are unique per school, not globally) and which is what a
    # school actually does.
    admin = make_staff(
        school_id, role=ROLE_ADMIN, employee_no="EMP-0001", **plan["admin"]
    )
    teachers = [
        make_staff(school_id, role=ROLE_TEACHER, employee_no=f"EMP-{number:04d}",
                   **teacher)
        for number, teacher in enumerate(plan["teachers"], start=2)
    ]
    pending = (
        make_staff(school_id, role=ROLE_TEACHER, approved=False, **plan["pending"])
        if plan.get("pending")
        else None
    )
    accounts = [admin, *teachers]
    if pending:
        accounts.append(pending)
    db.add_all(accounts)
    await db.flush()

    # ── School year ──────────────────────────────────────────────────────────
    # Both schools run the same calendar, and "2026-2027" is unique per school
    # rather than globally, so each gets its own row.
    year = SchoolYear(
        school_id=school_id,
        year="2026-2027",
        start_date=date(2026, 6, 1),
        end_date=date(2027, 3, 31),
        is_current=True,
    )
    previous = SchoolYear(
        school_id=school_id,
        year="2025-2026",
        start_date=date(2025, 6, 2),
        end_date=date(2026, 3, 31),
        is_current=False,
    )
    db.add_all([year, previous])
    await db.flush()

    # ── Grade levels, sections, subjects ─────────────────────────────────────
    levels = []
    for index, number in enumerate(plan["levels"], start=1):
        levels.append(
            GradeLevel(
                school_id=school_id,
                level=str(number),
                name=f"Grade {number}",
                sort_order=index,
            )
        )
    db.add_all(levels)
    await db.flush()

    section_names = plan["sections"]
    sections = []
    for level in levels:
        for name in section_names[int(level.level)]:
            sections.append(
                Section(
                    school_id=school_id,
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
        for name, code, total in plan["subjects"]:
            subjects.append(
                Subject(
                    school_id=school_id,
                    grade_level_id=level.grade_level_id,
                    subject_name=name,
                    subject_code=f"{code}{level.level}",
                    total_paces=total,
                    is_active=True,
                )
            )
    db.add_all(subjects)
    await db.flush()

    # ── Teacher assignments (one adviser per section) ────────────────────────
    assignments = []
    for index, section in enumerate(sections):
        adviser = teachers[index % len(teachers)]
        assignments.append(
            TeacherAssignment(
                school_id=school_id,
                teacher_id=adviser.id,
                section_id=section.section_id,
                is_adviser=True,
            )
        )
    db.add_all(assignments)
    await db.flush()

    # ── Timetable for the first few sections ─────────────────────────────────
    schedules = []
    for section in sections[:plan["timetable_sections"]]:
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
                        school_id=school_id,
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

    # ── Students, enrolments, PACE records ───────────────────────────────────
    # Roughly 14 weeks into a 40-week year, so ~4 of 12 booklets expected.
    weeks_elapsed = 14
    total_weeks = 36

    adviser_names = {
        a.section_id: next(t for t in teachers if t.id == a.teacher_id).full_name
        for a in assignments
    }

    student_count = 0
    warning_count = 0
    for section in sections:
        level = next(
            l for l in levels if l.grade_level_id == section.grade_level_id
        )
        level_subjects = [
            s for s in subjects if s.grade_level_id == section.grade_level_id
        ]
        roster_size = random.randint(*plan["roster"])

        for _ in range(roster_size):
            gender = random.choice(["Male", "Female"])
            first = random.choice(
                FIRST_NAMES_M if gender == "Male" else FIRST_NAMES_F
            )
            last = random.choice(LAST_NAMES)
            guardian_first = random.choice(FIRST_NAMES_M + FIRST_NAMES_F)
            age = 12 + int(level.level) - 7

            student = Student(
                school_id=school_id,
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
                school_id=school_id,
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
                # A StudentPace row is written for both schools even though only
                # one of them counts booklets: the row is also where the DepEd
                # class record lives, and that is what the risk model reads. A
                # school with `tracks_units = False` simply never shows the
                # unit columns — the screens hide them, the data stays uniform.
                pace = StudentPace(
                    school_id=school_id,
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
                    recorded_by=admin.id,
                    **assessment_record(profile),
                )
                db.add(pace)

                if evaluation["risk_level"] != "low":
                    warning_count += 1
                    db.add(
                        EarlyWarning(
                            school_id=school_id,
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

    return {
        "school": school,
        "admin": admin,
        "teachers": teachers,
        "pending": pending,
        "sections": len(sections),
        "subjects": len(subjects),
        "students": student_count,
        "warnings": warning_count,
    }


async def seed() -> None:
    async with AsyncSessionLocal() as db:
        existing = (await db.execute(select(func.count()).select_from(Staff))).scalar_one()
        if existing:
            print(f"Database already has {existing} staff account(s) — nothing to do.")
            print("Run `python seed.py --reset` to rebuild the demo data.")
            return

        # ── The operator account ─────────────────────────────────────────────
        # Created before the schools because it is the account that approves
        # them: a full seed should leave a demo that is usable immediately,
        # including the one screen only this role can open.
        superadmin = make_super_admin(SUPERADMIN_EMAIL, PASSWORD)
        db.add(superadmin)
        await db.flush()

        results = [
            await build_school(db, plan, approved_by=superadmin.id)
            for plan in SCHOOL_PLANS
        ]

        await db.commit()

        print("Demo data created.")
        print()
        print("System operator (manages schools and accounts only):")
        print(f"  {SUPERADMIN_EMAIL}  / {PASSWORD}")
        print("  Belongs to no school, so it cannot open a student record at all —")
        print("  its school_id is NULL and every student query is scoped by school.")
        print("  Use it to review, approve or deactivate the two schools below.")

        for index, result in enumerate(results, start=1):
            school = result["school"]
            units = (
                f"tracks {school.unit_label_plural} — "
                f"{school.default_units_per_subject} per subject"
                if school.tracks_units
                else "no unit tracking (DepEd class record only)"
            )
            print()
            print(f"School {index}: {school.name}")
            print(f"  {school.school_type}, {school.city} · {school.status}")
            print(f"  {units} · passing mark {school.passing_mark:g}")
            print(
                f"  sections {result['sections']} · subjects {result['subjects']} · "
                f"students {result['students']} · warnings {result['warnings']}"
            )
            print(f"  {result['admin'].email:<26} / {PASSWORD}   (principal)")
            for teacher in result["teachers"]:
                print(f"  {teacher.email:<26} / {PASSWORD}   (teacher)")
            if result["pending"]:
                print(
                    f"  {result['pending'].email:<26} / {PASSWORD}   "
                    f"(teacher, awaiting the principal's approval)"
                )

        print()
        print("Neither school can see the other's students, sections or warnings:")
        print("every row above carries its own school_id and every query is")
        print("filtered by the signed-in account's school.")
        print()
        print("First sign-in on a new browser asks for a 6-digit code — it is")
        print("printed in this server's console and, while DEBUG_OTP is on, shown")
        print("on the verification screen itself.")


async def create_super_admin(email: str, password: str | None) -> None:
    """Add one system-operator account to an existing database.

    Separate from seeding on purpose: this is the command a real deployment
    runs once, against a database that already holds real schools, so it must
    never touch anything else. It refuses to overwrite an existing account —
    quietly resetting a live operator's password from a seed script is not a
    behaviour anyone wants to discover afterwards.
    """
    # Sign-in lower-cases the address before looking it up, so an account
    # stored with capitals could never be signed into. The same reasoning
    # applies to the domain, which is why it is validated before anything is
    # written — see check_signinable.
    email = check_signinable(email.strip().lower())
    generated = password is None
    password = password or secrets.token_urlsafe(12)

    async with AsyncSessionLocal() as db:
        existing = (
            await db.execute(select(Staff).where(Staff.email == email))
        ).scalar_one_or_none()
        if existing:
            print(f"An account already exists for {email} (role: {existing.role}).")
            print("Nothing was changed. Use a different address, or reset the")
            print("password through the application rather than from here.")
            return

        account = make_super_admin(email, password)
        db.add(account)
        await db.commit()

        print(f"Super admin created: {email}")
        print(f"  password : {password}")
        if generated:
            print("  This password was generated now and is NOT stored anywhere in")
            print("  readable form — it will not be shown again. Copy it before you")
            print("  close this terminal.")
        print("  The account manages schools and accounts only; it belongs to no")
        print("  school and cannot open student records.")


async def score() -> None:
    """Ask the real AI service to score every seeded record.

    The predictions are genuine model output; only the inputs are invented. No
    prediction is ever written by hand here — if the service cannot be reached
    the rows are marked ``source="fallback"`` exactly as they would be in
    production, and this prints how many, so a demo is never quietly showing
    rule-based numbers under an AI label.

    It scores every school's records in one pass, which is right for an
    operator-side batch job: the predictions it writes are scoped per row, not
    per run.
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

        fallback, versions, reason = 0, set(), None
        batch = 500
        for start in range(0, len(pairs), batch):
            part = pairs[start:start + batch]
            results = await ml_client.predict_many(part)
            for (row, _label), result in zip(part, results):
                if result.source == "fallback":
                    fallback += 1
                    reason = reason or result.error
                else:
                    versions.add(result.model_version)
                db.add(
                    AIRiskPrediction(
                        # Taken from the PACE row rather than from any notion of
                        # "the current school": a prediction belongs to the same
                        # tenant as the record it was made from.
                        school_id=row.school_id,
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
                f"  WARNING: {fallback} of them fell back to the PACE rules rather "
                f"than the model."
            )
            print(f"    service : {ml_client.settings.AI_SERVICE_URL}")
            print(f"    token   : {'set' if ml_client.settings.AI_SERVICE_TOKEN else 'NOT SET'}")
            print(f"    reason  : {reason}")
            print("  They are labelled 'rule-based' in the UI, not passed off as model")
            print("  output. Fix the cause above, then either re-run this with")
            print("  --score-only or press 'Re-score records' on the AI Risk Model page.")
        else:
            print(f"  All scored by the model: {', '.join(sorted(versions))}")


async def reset() -> None:
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    print("All tables dropped.")


# The two schools the demonstration seed creates. Nothing else is matched, so
# a purge cannot touch a school a principal registered.
DEMO_SCHOOL_NAMES = [plan["name"] for plan in SCHOOL_PLANS]


async def fresh_start(email: str, password: str | None) -> None:
    """Empty the database completely and leave one operator account behind.

    Every table is dropped and rebuilt, so no demonstration learner, class
    record, prediction or school survives. The operator account is recreated
    immediately afterwards because without it nobody can sign in to approve
    the first real school.
    """
    import app.models  # noqa: F401  (registers every table before the drop)

    await reset()
    await init_models()
    await create_super_admin(email, password)
    print()
    print("The database is empty apart from that account.")
    print("Sign in with it, then register your first real school from the")
    print("sign-in page: 'Request a staff account' -> 'a school administrator'.")


async def purge_demo() -> None:
    """Delete only the demonstration schools, leaving real ones alone.

    For a database that already holds a real school. Everything belonging to
    a demonstration school goes with it — learners, enrolments, class
    records, warnings, predictions — because every tenant table carries a
    school_id and the foreign keys cascade from the school row.
    """
    from app.models.schools import School

    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                select(School).where(School.name.in_(DEMO_SCHOOL_NAMES))
            )
        ).scalars().all()

        if not rows:
            print("No demonstration schools found — nothing to remove.")
        for school in rows:
            print(f"Removing {school.name} (school_id={school.school_id}) "
                  "and everything belonging to it…")
            await db.delete(school)

        # Staff attached to no school are operator accounts and are kept; the
        # demonstration teachers and principals went with their schools.
        await db.commit()

        remaining = (
            await db.execute(select(School.name).order_by(School.name))
        ).scalars().all()
        print()
        print(f"Schools remaining: {len(remaining)}")
        for name in remaining:
            print(f"  - {name}")


async def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Database setup for SmartPACE. Creating demonstration data is no "
            "longer the default: it has to be asked for with --demo, so that "
            "running this script against the live database cannot put "
            "invented learners into it."
        )
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="create the two demonstration schools and their invented "
             "learners (development and screenshots only)",
    )
    parser.add_argument(
        "--fresh-start",
        action="store_true",
        help="empty the database completely and leave one operator account; "
             "this is how to clear every trace of the demonstration data",
    )
    parser.add_argument(
        "--purge-demo",
        action="store_true",
        help="delete only the two demonstration schools, leaving any real "
             "school and its records untouched",
    )
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
    parser.add_argument(
        "--create-superadmin",
        metavar="EMAIL",
        help="create a system-operator account for this address and exit, "
             "seeding nothing",
    )
    parser.add_argument(
        "--password",
        help="password for --create-superadmin (a strong one is generated and "
             "printed if omitted)",
    )
    args = parser.parse_args()

    # Checked before everything else: these are the commands run against a
    # real database holding real schools, so none of them may drag seeding or
    # scoring along behind it.
    #
    # --fresh-start comes first because it accepts --create-superadmin as the
    # address for the account it leaves behind; tested the other way round,
    # that combination would quietly create an account and never empty
    # anything.
    if args.fresh_start:
        await fresh_start(args.create_superadmin or SUPERADMIN_EMAIL, args.password)
        await async_engine.dispose()
        return

    if args.create_superadmin:
        # Tables may not exist yet on a fresh deployment, and this creates only
        # what is missing.
        await init_models()
        await create_super_admin(args.create_superadmin, args.password)
        await async_engine.dispose()
        return

    if args.purge_demo:
        await purge_demo()
        await async_engine.dispose()
        return

    if args.score_only:
        await score()
        await async_engine.dispose()
        return

    # Seeding is now opt-in. Running this script with no arguments used to
    # fill the database with invented schools and learners, which is exactly
    # how demonstration data ends up in a live deployment.
    if not args.demo:
        parser.print_help()
        print()
        print("Nothing was changed. Pick one:")
        print("  --fresh-start   empty the database, keep one operator account")
        print("  --purge-demo    remove only the demonstration schools")
        print("  --demo          create the demonstration data (development only)")
        print("  --score-only    re-score the records already stored")
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
