"""Database setup for SmartPACE.

    python seed.py --fresh-start    empty everything, keep one operator account
    python seed.py --demo           add the small demonstration school
    python seed.py --score          as --demo, then ask the model to score it
    python seed.py --score-only     re-score whatever is already stored
    python seed.py --purge-demo     remove the demonstration school only

Running it with no arguments does nothing and prints this list. That is
deliberate: a bare `python seed.py` used to fill the database with invented
schools and learners, which is how demonstration data ends up in a live
deployment and stays there.

What --demo creates
-------------------
One school, one principal, two teachers, ten learners in one section, two
subjects, and a class record for each learner in each subject. Small enough
to read end to end on screen, large enough that every screen in the system
has something real to show.

Every learner here is invented. The names, the scores and the guardians are
made up, and nothing in this file is ever used to train or evaluate the risk
model — that is done in the ml/ scripts, against the school's own records.
The predictions this script can write are genuine model output; only the
inputs are invented.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import secrets
from datetime import date, datetime, timezone

from sqlalchemy import select

from app.core.security import get_password_hash
from app.db.session import AsyncSessionLocal, Base, async_engine, init_models
from app.models.academic import GradeLevel, SchoolYear, Section, Subject, TeacherAssignment
from app.models.schools import SCHOOL_APPROVED, TYPE_PUBLIC, School
from app.models.staff import (
    ROLE_ADMIN,
    ROLE_SUPER_ADMIN,
    ROLE_TEACHER,
    STATUS_APPROVED,
    Staff,
)
from app.models.students import (
    ClassRecordSetup,
    EarlyWarning,
    Student,
    StudentEnrollment,
    StudentPace,
)
from app.services import grading, risk

PASSWORD = "Lbca@2026"

# `example.com` rather than something like `system.local`: `.local` is reserved
# for multicast DNS and the address validation the API applies at sign-in
# refuses reserved domains outright, so an account created there could be
# written to the database and then never signed into. `example.com` is
# reserved by RFC 2606 for documentation, so it can never belong to anyone and
# it passes validation.
SUPERADMIN_EMAIL = "superadmin@example.com"

DEMO_SCHOOL_NAME = "Looc National High School"
# The mark the risk bands are set from; the school owns this.
PASSING_MARK = 75.0
# The line the system flags against: a learner heading for a final subject
# grade below this is the one an early warning is for.
RISK_LINE = 80.0
LAST_TERM = max(grading.TERMS)
# A point of drift per term, so the three terms are not carbon copies and a
# year average is worth computing. Small on purpose: the school's own
# records show learners move by a median of one point between terms.
TERM_DRIFT = {1: 0, 2: -1, 3: 1}

# Highest possible score per assessment. The same sheet shape as the
# MATATAG workbook: five written works, three performance tasks, two
# summative tests and a term examination.
WW_HIGHEST = [20.0, 20.0, 15.0, 15.0, 15.0]     # 85 in total
PT_HIGHEST = [25.0, 25.0, 25.0]                 # 75
EXAM_HIGHEST = [25.0, 25.0, 50.0]               # ST1, ST2, TE

# Four subjects across BOTH class records, because a school runs both at
# once and the system has to show that it does. The weights come from the
# template, not from here — see app/services/grading.py.
# The last field nudges every mark in that subject up or down a point, so a
# learner is not identical across all four. Kept small: the point is that a
# learner can be fine in one subject and not in another, not that the
# demonstration should look like a failing cohort.
SUBJECTS = [
    ("Filipino", "FIL", grading.CORE, 0),
    ("Science", "SCI", grading.CORE, -1),
    ("Music and Arts", "MA", grading.MAPEH, +1),
    ("Physical Education and Health", "PEH", grading.MAPEH, -1),
]

# Ten learners. The scores are written out rather than generated at random so
# that the demonstration is the same every time it is seeded — a screenshot
# taken today still matches the database next week, and a figure quoted in
# the manuscript does not move underneath it.
#
# Columns: first, last, sex, five written works, three performance tasks,
#          [ST1, ST2, term examination].
# A None is a blank cell: the assessment has not been taken or not yet marked.
LEARNERS = [
    # Tuned so the demonstration shows every band rather than a failing
    # cohort: four comfortably on track, two to watch, two at risk and two
    # below the passing mark. Those are the proportions a teacher would
    # recognise, and they exercise every colour on every screen.
    ("Althea Mae",   "Bontigao", "Female", [18, 17, 14, 13, 14], [23, 22, 24], [22, 23, 44]),
    ("Jerome",       "Cabanlit", "Male",   [11,  9,  8,  7,  9], [14, 13, 12], [12, 11, 23]),
    ("Kyla Marie",   "Dagohoy",  "Female", [19, 18, 14, 14, 15], [24, 24, 23], [23, 24, 47]),
    # Just under the passing mark: the case the whole system exists for.
    ("Mark Anthony", "Escobido", "Male",   [15, 14, 11, 11, 11], [19, 18, 20], [17, 18, 35]),
    # Passing, but inside the band the model targets.
    ("Shaira",       "Fuentes",  "Female", [16, 15, 12, 12, 13], [21, 20, 21], [19, 20, 39]),
    ("Paulo",        "Gumapac",  "Male",   [ 9,  8,  7,  6,  8], [13, 12, 11], [10, 12, 21]),
    ("Jenny Rose",   "Lumabao",  "Female", [17, 16, 13, 13, 12], [22, 21, 22], [20, 21, 41]),
    # Comfortable in most subjects, slipping in the weaker one.
    ("Rhoda",        "Makinano", "Female", [16, 15, 12, 11, 13], [20, 20, 21], [19, 19, 38]),
    ("Carl Jason",   "Nunez",    "Male",   [20, 19, 15, 14, 15], [25, 24, 25], [24, 25, 48]),
    # Coursework complete, no examinations sat yet, so there is no term
    # grade. That is the ordinary mid-term state and the only one in which
    # an early warning has any point at all.
    ("Trisha Mae",   "Olvido",   "Female", [15, 14, 12, 11, 12], [20, 19, 20], [None, None, None]),
]


def _now() -> datetime:
    return datetime.now(timezone.utc)


# Fields this script needs back from app.services.grading.compute. Checked
# once, up front, because the alternative is a KeyError five hundred lines
# into a run with half the demonstration already written — and because the
# real cause is almost always a half-copied set of files, which the error
# alone does not say.
REQUIRED_FROM_GRADING = (
    "ww_total_raw", "ww_percent", "ww_weighted",
    "pt_total_raw", "pt_percent", "pt_weighted",
    "exam_parts", "exam_percent", "exam_weighted",
    "initial_grade", "transmuted_grade", "descriptor",
    "pre_exam_weighted", "pre_exam_max", "complete",
)


def check_grading_module() -> None:
    """Fail early and legibly if grading.py is older than this script."""
    sample = grading.compute(
        ww_scores=[1], pt_scores=[1], exam_scores=[1, 1, 1],
        ww_highest=[1], pt_highest=[1], exam_highest=[1, 1, 1],
        template=grading.CORE,
    )
    missing = [f for f in REQUIRED_FROM_GRADING if f not in sample]
    if missing:
        raise SystemExit(
            "app/services/grading.py is out of date: compute() does not "
            f"return {', '.join(missing)}.\n"
            "Copy the current app/services/grading.py into place and run "
            "this again. Nothing was written."
        )


# ── Guards ───────────────────────────────────────────────────────────────────
RESERVED_TLDS = {"local", "localhost", "example", "invalid", "test"}


def check_signinable(email: str) -> str:
    """Refuse an address the API's own sign-in validation would reject.

    Catching it here means a bad address fails while the account is being
    created, rather than becoming an account nobody can ever sign in to.
    `example.com` is fine — it is the reserved *domain* example.com, not the
    reserved top-level domain `.example`.
    """
    if "@" not in email:
        raise SystemExit(f"'{email}' is not an email address.")
    domain = email.rsplit("@", 1)[1].lower().strip(".")
    tld = domain.rsplit(".", 1)[-1] if "." in domain else domain
    if tld in RESERVED_TLDS:
        raise SystemExit(
            f"'{email}' uses the reserved top-level domain '.{tld}', which the "
            f"API refuses at sign-in. Use a real domain, or "
            f"{SUPERADMIN_EMAIL}."
        )
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


def make_staff(school_id, email, first, last, role, contact, department, employee_no):
    """One school's staff account.

    ``school_id`` is first and required because it is the field that decides
    what this person can ever see — omitting it is the one mistake in this
    script that would quietly break tenant isolation.
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
        account_status=STATUS_APPROVED,
        is_approved=True,
        is_active=True,
        approved_at=_now(),
    )


# ── The demonstration school ─────────────────────────────────────────────────
async def build_demo(db, operator_id) -> dict:
    """One school, two teachers, ten learners, two subjects."""
    school = School(
        name=DEMO_SCHOOL_NAME,
        short_name="LNHS",
        school_type=TYPE_PUBLIC,
        address="Looc, Barangay Looc",
        city="Cagayan de Oro",
        province="Misamis Oriental",
        region="Region X (Northern Mindanao)",
        contact_number="(088) 855 0199",
        contact_email="office@lnhs.deped.gov.ph",
        # A DepEd school: no PACE booklets, so the unit screens stay hidden
        # and the class record is the only place marks are entered.
        tracks_units=False,
        passing_mark=PASSING_MARK,
        grading_periods=3,
        status=SCHOOL_APPROVED,
        approved_at=_now(),
        approved_by=operator_id,
    )
    db.add(school)
    await db.flush()                      # the generated id is needed below
    sid = school.school_id

    principal = make_staff(
        sid, "principal@lnhs.deped.gov.ph", "Remedios", "Alcantara",
        ROLE_ADMIN, "0917 300 1001", "School Administration", "EMP-0001",
    )
    teachers = [
        make_staff(sid, "teacher1@lnhs.deped.gov.ph", "Marivic", "Bermudez",
                   ROLE_TEACHER, "0917 300 1002", "Filipino", "EMP-0002"),
        make_staff(sid, "teacher2@lnhs.deped.gov.ph", "Dennis", "Carcedo",
                   ROLE_TEACHER, "0917 300 1003", "Araling Panlipunan", "EMP-0003"),
    ]
    db.add_all([principal, *teachers])
    await db.flush()

    year = SchoolYear(
        school_id=sid, year="2026-2027",
        start_date=date(2026, 6, 1), end_date=date(2027, 3, 31),
        is_current=True,
    )
    db.add(year)
    await db.flush()

    level = GradeLevel(school_id=sid, level="8", name="Grade 8", sort_order=1)
    db.add(level)
    await db.flush()

    section = Section(
        school_id=sid,
        grade_level_id=level.grade_level_id,
        section_code="G8-CAT",
        name="Grade 8 - Cattleya",
        room="Room 801",
        capacity=40,
    )
    db.add(section)
    await db.flush()

    subjects = [
        Subject(school_id=sid, grade_level_id=level.grade_level_id,
                subject_name=name, subject_code=f"{code}8",
                total_paces=0, is_active=True)
        for name, code, _template, _shift in SUBJECTS
    ]
    db.add_all(subjects)
    await db.flush()
    template_of = {name: tpl for name, _code, tpl, _s in SUBJECTS}
    shift_of = {name: shift for name, _code, _tpl, shift in SUBJECTS}

    # The core subjects belong to the first teacher, MAPEH to the second;
    # the first is also the section's adviser.
    db.add_all([
        TeacherAssignment(school_id=sid, teacher_id=teachers[0].id,
                          section_id=section.section_id, is_adviser=True),
        TeacherAssignment(school_id=sid, teacher_id=teachers[1].id,
                          section_id=section.section_id, is_adviser=False),
    ])

    # One class-record header per subject per term. Without it there is no
    # denominator, so every percentage would stay blank.
    for subject in subjects:
        tpl = template_of[subject.subject_name]
        profile = grading.TEMPLATES[tpl]
        for period in grading.TERMS:
            db.add(ClassRecordSetup(
                school_id=sid,
                section_id=section.section_id,
                subject_id=subject.subject_id,
                grading_period=period,
                template=tpl,
                ww_highest=json.dumps(WW_HIGHEST),
                pt_highest=json.dumps(PT_HIGHEST),
                exam_highest=json.dumps(EXAM_HIGHEST),
                ww_weight=profile["ww_weight"],
                pt_weight=profile["pt_weight"],
                ex_weight=profile["ex_weight"],
                exam_subweights=json.dumps(list(grading.EXAM_SUBWEIGHTS)),
                updated_by=principal.id,
            ))

    at_risk = 0
    flagged = 0
    records = 0
    for index, (first, last, sex, ww, pt, exam) in enumerate(LEARNERS, start=1):
        student = Student(
            school_id=sid,
            first_name=first,
            last_name=last,
            gender=sex,
            birth_date=date(2013, (index % 12) + 1, (index % 28) + 1),
            address=f"Purok {index}, Barangay Looc, Cagayan de Oro",
            guardian_first_name="Maria" if index % 2 else "Roberto",
            guardian_last_name=last,
            guardian_contact=f"0917 300 2{index:03d}",
            guardian_relationship="Parent",
            created_by=principal.id,
        )
        db.add(student)
        await db.flush()
        student.login_id = f"S{student.student_id:04d}"

        enrollment = StudentEnrollment(
            school_id=sid,
            student_id=student.student_id,
            grade_level_id=level.grade_level_id,
            section_id=section.section_id,
            school_year_id=year.school_year_id,
            enrolled_by=principal.id,
            enrollment_date=date(2026, 6, 3),
            is_active=True,
        )
        db.add(enrollment)
        await db.flush()

        for subject in subjects:
            tpl = template_of[subject.subject_name]
            profile = grading.TEMPLATES[tpl]
            base_shift = shift_of[subject.subject_name]
            teacher = teachers[0 if tpl == grading.CORE else 1]
            term_grades: list = []

            # Terms 1 and 2 are finished; the third is still being taught,
            # so it carries coursework and no examinations. That is the
            # state a school is actually in when an early warning is worth
            # anything, and it is the state the screens have to handle: a
            # year average from two finished terms, plus one in progress.
            for period in grading.TERMS:
                shift = base_shift + TERM_DRIFT[period]
                bump = [None if v is None else max(0, v + shift) for v in ww]
                bump_pt = [None if v is None else max(0, v + shift) for v in pt]
                in_progress = period == LAST_TERM
                bump_ex = ([None, None, None] if in_progress
                           else [None if v is None else max(0, v + shift) for v in exam])

                # Computed by the same module the API uses, so a seeded
                # grade is identical to one a teacher would get by typing
                # these marks in by hand.
                derived = grading.compute(
                    ww_scores=bump, pt_scores=bump_pt, exam_scores=bump_ex,
                    ww_highest=WW_HIGHEST, pt_highest=PT_HIGHEST,
                    exam_highest=EXAM_HIGHEST, template=tpl,
                    ww_weight=profile["ww_weight"],
                    pt_weight=profile["pt_weight"],
                    ex_weight=profile["ex_weight"],
                )
                term_grades.append(derived["transmuted_grade"])

                pace = StudentPace(
                    school_id=sid,
                    student_id=student.student_id,
                    enrollment_id=enrollment.enrollment_id,
                    subject_id=subject.subject_id,
                    subject=subject.subject_name,
                    grading_period=period,
                    # This school counts no booklets; the row exists
                    # because it is also where the class record lives.
                    current_pace=0, expected_pace=0, total_paces=0,
                    pace_percent=0.0, paces_behind=0, attendance=100.0,
                    average_score=derived["transmuted_grade"] or 0.0,
                    recorded_by=teacher.id,
                    ww_scores=json.dumps(bump),
                    pt_scores=json.dumps(bump_pt),
                    exam_scores=json.dumps(bump_ex),
                    exam_parts=json.dumps(derived["exam_parts"]),
                    record_complete=bool(derived["complete"]),
                    **{k: derived[k] for k in (
                        "ww_total_raw", "ww_percent", "ww_weighted",
                        "pt_total_raw", "pt_percent", "pt_weighted",
                        "exam_percent", "exam_weighted",
                        "initial_grade", "transmuted_grade", "descriptor",
                        "pre_exam_weighted", "pre_exam_max")},
                )
                db.add(pace)
                await db.flush()
                records += 1

            # One flag per subject, judged on the YEAR rather than on any
            # single term — the final grade is the mean of the terms, so
            # that is what the system was asked about. Written here the way
            # the save path writes it, through the same scoring function,
            # so a freshly seeded demonstration opens with the list it
            # would have had if a teacher had typed every mark in.
            evaluation = risk.evaluate_year(
                pace, term_grades, RISK_LINE, PASSING_MARK
            )
            outlook = evaluation["outlook"]
            if outlook["year_average"] is not None and outlook["year_average"] < RISK_LINE:
                at_risk += 1
            if evaluation["risk_level"] != "low":
                flagged += 1
                db.add(EarlyWarning(
                    school_id=sid,
                    student_id=student.student_id,
                    enrollment_id=enrollment.enrollment_id,
                    subject=subject.subject_name,
                    teacher=teacher.full_name,
                    risk_level=evaluation["risk_level"],
                    risk_score=evaluation["risk_score"],
                    paces_behind=0,
                    pace_percent=0.0,
                    standing=evaluation["standing"],
                    basis=evaluation["basis"],
                    status=evaluation["status"],
                    trend="stable",
                    last_activity="Today",
                    intervention=evaluation["intervention"],
                ))


    return {
        "school": school.name,
        "learners": len(LEARNERS),
        "records": records,
        "subjects": ", ".join(
            f"{name} ({grading.TEMPLATES[tpl]['short']})"
            for name, _code, tpl, _s in SUBJECTS),
        "at_risk": at_risk,
        "flagged": flagged,
        "principal": principal.email,
        "teachers": [t.email for t in teachers],
    }


async def demo() -> None:
    async with AsyncSessionLocal() as db:
        existing = (
            await db.execute(select(School).where(School.name == DEMO_SCHOOL_NAME))
        ).scalar_one_or_none()
        if existing is not None:
            print(f"{DEMO_SCHOOL_NAME} is already in the database — nothing to do.")
            print("Run `python seed.py --purge-demo` first to rebuild it.")
            return

        operator = (
            await db.execute(select(Staff).where(Staff.role == ROLE_SUPER_ADMIN))
        ).scalars().first()
        if operator is None:
            operator = make_super_admin(SUPERADMIN_EMAIL, PASSWORD)
            db.add(operator)
            await db.flush()
            made_operator = True
        else:
            made_operator = False

        summary = await build_demo(db, operator.id)
        await db.commit()

        print("Demonstration data created. Every learner in it is invented.")
        print()
        print(f"  School     {summary['school']} (public, no unit tracking)")
        print(f"  Learners   {summary['learners']} in Grade 8 - Cattleya")
        print(f"  Subjects   {summary['subjects']}")
        print(f"  Records    {summary['records']} class records, first term "
              f"({summary['at_risk']} below a term grade of 80)")
        print(f"  Warnings   {summary['flagged']} subject flags on the "
              f"early-warning list")
        print()
        print(f"  Password for every account below: {PASSWORD}")
        if made_operator:
            print(f"  Operator   {SUPERADMIN_EMAIL}")
            print("             manages schools and accounts; cannot open a "
                  "student record anywhere")
        print(f"  Principal  {summary['principal']}")
        for email in summary["teachers"]:
            print(f"  Teacher    {email}")


# ── Scoring ──────────────────────────────────────────────────────────────────
async def score() -> None:
    """Ask the real AI service to score every stored class record.

    The predictions are genuine model output; only the inputs are invented.
    Nothing is written by hand — if the service cannot be reached the rows are
    marked ``source="fallback"`` exactly as they would be in production, and
    this prints how many, so a demonstration never quietly shows rule-based
    numbers under a model label.
    """
    from app.models.students import AIRiskPrediction
    from app.services import ml_client

    async with AsyncSessionLocal() as db:
        # The model reads a learner's graded first term and predicts the final
        # grade, so those are the only records it is asked about — the same
        # rule the app's save and re-score paths follow.
        rows = [
            row for row in (await db.execute(select(StudentPace))).scalars().all()
            if ml_client.first_term_ready(row)
        ]
        if not rows:
            print("Nothing to score — no graded first-term records yet.")
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
            (row, levels.get(getattr(enrollments.get(row.enrollment_id),
                                     "grade_level_id", None)))
            for row in rows
        ]

        fallback, versions, reason = 0, set(), None
        for start in range(0, len(pairs), 500):
            part = pairs[start:start + 500]
            for (row, _label), result in zip(part, await ml_client.predict_many(part)):
                if result.source == "fallback":
                    fallback += 1
                    reason = reason or result.error
                else:
                    versions.add(result.model_version)
                db.add(AIRiskPrediction(
                    # Taken from the record rather than from any notion of
                    # "the current school": a prediction belongs to the same
                    # tenant as the record it was made from.
                    school_id=row.school_id,
                    student_id=row.student_id,
                    enrollment_id=row.enrollment_id,
                    pace_id=row.pace_id,
                    subject=row.subject,
                    **result.as_columns(),
                ))
        await db.commit()

        print(f"Scored {len(pairs)} class record(s).")
        if fallback:
            print(f"  WARNING: {fallback} fell back to the rule rather than the model.")
            print(f"    service : {ml_client.settings.AI_SERVICE_URL}")
            print(f"    token   : "
                  f"{'set' if ml_client.settings.AI_SERVICE_TOKEN else 'NOT SET'}")
            print(f"    reason  : {reason}")
            print("  They are labelled rule-based in the interface, not passed off")
            print("  as model output. Fix the cause above, then re-run with")
            print("  --score-only or press 'Re-score records' on the AI Risk page.")
        else:
            print(f"  All scored by the model: {', '.join(sorted(versions))}")


# ── Emptying ─────────────────────────────────────────────────────────────────
async def reset() -> None:
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    print("All tables dropped.")


async def create_super_admin(email: str, password: str | None) -> None:
    password = password or (secrets.token_urlsafe(12) + "A1!")
    async with AsyncSessionLocal() as db:
        existing = (
            await db.execute(select(Staff).where(Staff.email == email))
        ).scalar_one_or_none()
        if existing is not None:
            print(f"An account already exists for {email} — nothing created.")
            return
        db.add(make_super_admin(email, password))
        await db.commit()
    print(f"System operator created: {email}")
    print(f"Password: {password}")
    print("Save it now. It is not stored anywhere in readable form.")


async def fresh_start(email: str, password: str | None) -> None:
    """Empty the database completely and leave one operator account behind.

    Every table is dropped and rebuilt, so no demonstration learner, class
    record, prediction or school survives. The operator is recreated straight
    afterwards because without it nobody can sign in to approve the first
    real school.

    This is also how to apply a schema change: ``create_all`` adds missing
    tables but never adds a column to a table that already exists, so after
    the model gains a column this is what puts it in the database.
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
    """Delete the demonstration school, leaving any real one alone.

    Everything belonging to it goes with it — learners, enrolments, class
    records, warnings, predictions — because every tenant table carries a
    school_id and the foreign keys cascade from the school row.
    """
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(select(School).where(School.name == DEMO_SCHOOL_NAME))
        ).scalars().all()
        if not rows:
            print("No demonstration school found — nothing to remove.")
        for school in rows:
            print(f"Removing {school.name} (school_id={school.school_id}) "
                  f"and everything belonging to it…")
            await db.delete(school)
        await db.commit()

        remaining = (
            await db.execute(select(School.name).order_by(School.name))
        ).scalars().all()
        print(f"\nSchools remaining: {len(remaining)}")
        for name in remaining:
            print(f"  - {name}")


# ── Command line ─────────────────────────────────────────────────────────────
async def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Database setup for SmartPACE. Creating demonstration data is not "
            "the default: it has to be asked for with --demo, so that running "
            "this script against the live database cannot put invented "
            "learners into it."
        )
    )
    parser.add_argument("--fresh-start", action="store_true",
                        help="empty the database and leave one operator account; "
                             "also how to apply a schema change")
    parser.add_argument("--demo", action="store_true",
                        help="add the demonstration school: 1 principal, "
                             "2 teachers, 10 learners")
    parser.add_argument("--purge-demo", action="store_true",
                        help="remove the demonstration school, leaving real ones")
    parser.add_argument("--score", action="store_true",
                        help="after --demo, ask the model to score every record")
    parser.add_argument("--score-only", action="store_true",
                        help="skip seeding; re-score what is already stored")
    parser.add_argument("--create-superadmin", metavar="EMAIL",
                        help="create an operator account for this address and exit")
    parser.add_argument("--password",
                        help="password for the operator account (a strong one is "
                             "generated and printed if omitted)")
    args = parser.parse_args()

    # --fresh-start is tested first because it accepts --create-superadmin as
    # the address for the account it leaves behind; the other way round, that
    # combination would create an account and never empty anything.
    if args.fresh_start:
        await fresh_start(args.create_superadmin or SUPERADMIN_EMAIL, args.password)
    elif args.create_superadmin:
        # Tables may not exist yet on a fresh deployment; this adds only what
        # is missing and touches nothing that is already there.
        await init_models()
        await create_super_admin(args.create_superadmin, args.password)
    elif args.purge_demo:
        await purge_demo()
    elif args.score_only:
        await score()
    elif args.demo:
        check_grading_module()
        await init_models()
        await demo()
        if args.score:
            await score()
    else:
        parser.print_help()
        print("\nNothing was changed.")

    await async_engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())