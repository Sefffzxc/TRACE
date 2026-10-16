"""Read-side helpers that flatten the relational model into the shapes the
React screens consume, so the UI never has to stitch six lists together."""
from __future__ import annotations

from typing import Callable, Dict, Iterable, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.academic import GradeLevel, Section, TeacherAssignment
from app.models.staff import Staff
from app.models.students import (
    AIRiskPrediction,
    Student,
    StudentEnrollment,
    StudentPace,
)
from app.schemas.students import StudentResponse
from app.services import risk
from app.services.risk import evaluate_row


class Lookups:
    """One round-trip for every reference table the list views need."""

    def __init__(self) -> None:
        self.grade_levels: Dict[int, str] = {}
        self.sections: Dict[int, str] = {}
        self.section_grade: Dict[int, int] = {}
        self.advisers: Dict[int, str] = {}
        self.staff: Dict[str, str] = {}

    @classmethod
    async def load(cls, db: AsyncSession, *, school_id: int) -> "Lookups":
        self = cls()
        for level in (
            await db.execute(
                select(GradeLevel).where(GradeLevel.school_id == school_id)
            )
        ).scalars():
            self.grade_levels[level.grade_level_id] = level.name
        for section in (
            await db.execute(select(Section).where(Section.school_id == school_id))
        ).scalars():
            self.sections[section.section_id] = section.name
            self.section_grade[section.section_id] = section.grade_level_id
        # Staff.school_id is NULL for the super admin, so this filter also keeps
        # the operator account out of every school's name lookup.
        for person in (
            await db.execute(select(Staff).where(Staff.school_id == school_id))
        ).scalars():
            self.staff[str(person.id)] = person.full_name
        rows = (
            await db.execute(
                select(TeacherAssignment).where(
                    TeacherAssignment.school_id == school_id,
                    TeacherAssignment.is_adviser.is_(True),
                )
            )
        ).scalars().all()
        for assignment in rows:
            self.advisers[assignment.section_id] = self.staff.get(
                str(assignment.teacher_id), ""
            )
        return self


async def current_enrollments(
    db: AsyncSession,
    student_ids: Optional[Iterable[int]] = None,
    *,
    school_id: int,
) -> Dict[int, StudentEnrollment]:
    """The active enrolment per student, keyed by student id."""
    stmt = select(StudentEnrollment).where(
        StudentEnrollment.school_id == school_id,
        StudentEnrollment.is_active.is_(True),
    )
    if student_ids is not None:
        ids = list(student_ids)
        if not ids:
            return {}
        stmt = stmt.where(StudentEnrollment.student_id.in_(ids))
    rows = (await db.execute(stmt)).scalars().all()
    out: Dict[int, StudentEnrollment] = {}
    for row in rows:
        # Later school years win when a student has more than one active row.
        existing = out.get(row.student_id)
        if existing is None or row.school_year_id > existing.school_year_id:
            out[row.student_id] = row
    return out


async def paces_by_student(
    db: AsyncSession,
    student_ids: Optional[Iterable[int]] = None,
    *,
    school_id: int,
    passing_mark: float = 75.0,
    risk_line: float = 80.0,
    terms_per_year: Optional[int] = None,
    visible: Optional[Callable[[Optional[int], Optional[str]], bool]] = None,
) -> Dict[int, List[dict]]:
    """``visible(enrollment_id, subject)``, when given, keeps only the
    subjects the viewer may see (see app/services/access.py), so a subject
    teacher's summary of a learner is built from their own subjects."""
    stmt = select(StudentPace).where(StudentPace.school_id == school_id)
    if student_ids is not None:
        ids = list(student_ids)
        if not ids:
            return {}
        stmt = stmt.where(StudentPace.student_id.in_(ids))
    rows = (await db.execute(stmt)).scalars().all()
    if visible is not None:
        rows = [r for r in rows if visible(r.enrollment_id, r.subject)]

    # Every term of every subject, so each record can be judged on where its
    # YEAR is heading rather than on the term it happens to sit in.
    years = await term_grades_by_subject(
        db, school_id=school_id, student_ids=student_ids
    )

    out: Dict[int, List[dict]] = {}
    seen: set[tuple] = set()
    for row in rows:
        key = (row.enrollment_id, row.subject)
        # One entry per subject, not per term: a learner has one standing in
        # Filipino, not three.
        if key in seen:
            continue
        seen.add(key)
        # Scored the same way the save path scores it, so the figure on the
        # Students page is the one behind the flag on the early-warning list
        # rather than a second opinion computed from different inputs.
        evaluation = risk.evaluate_year(
            row, years.get(key, []), risk_line, passing_mark
        )
        outlook = evaluation.get("outlook") or {}
        out.setdefault(row.student_id, []).append(
            {
                "subject": row.subject,
                "pace_percent": row.pace_percent,
                "paces_behind": row.paces_behind,
                "risk_score": evaluation["risk_score"],
                "risk_level": evaluation["risk_level"],
                "standing": evaluation["standing"],
                "basis": evaluation["basis"],
                "term_grade": row.transmuted_grade,
                "descriptor": row.descriptor,
                "terms_done": outlook.get("terms_done", 0),
                "year_average": outlook.get("year_average"),
                "required": outlook.get("required"),
                # Against the passing mark rather than the risk line, over
                # the school's own number of terms: the figure a teacher
                # means by "what does this learner need to pass".
                "needs_to_pass": risk.required_average(
                    years.get(key, []), passing_mark,
                    terms_per_year or risk.TERMS_PER_YEAR,
                ),
                "reachable": outlook.get("reachable", True),
                "intervention": evaluation.get("intervention"),
            }
        )
    return out


def profile_missing(student: Student) -> list[str]:
    """The parts of a learner's profile nobody has filled in yet."""
    missing = []
    if not student.birth_date:
        missing.append("birth date")
    if not (student.address or "").strip():
        missing.append("address")
    if not (student.guardian_first_name or "").strip() or not (
        student.guardian_last_name or ""
    ).strip():
        missing.append("parent or guardian")
    if not (student.guardian_contact or "").strip():
        missing.append("guardian contact")
    return missing


def build_student_response(
    student: Student,
    enrollment: Optional[StudentEnrollment],
    pace_rows: List[dict],
    lookups: Lookups,
) -> StudentResponse:
    item = StudentResponse.model_validate(student)
    item.full_name = student.full_name
    item.profile_missing = profile_missing(student)

    if enrollment is not None:
        item.enrollment_id = enrollment.enrollment_id
        item.grade_level_id = enrollment.grade_level_id
        item.grade_level_name = lookups.grade_levels.get(enrollment.grade_level_id)
        item.section_id = enrollment.section_id
        item.section_name = lookups.sections.get(enrollment.section_id)
        item.school_year_id = enrollment.school_year_id
        item.adviser_name = lookups.advisers.get(enrollment.section_id)

    summary = risk.summarise(pace_rows)
    item.pace_percent = summary["pace_percent"]
    item.paces_behind = summary["paces_behind"]
    item.risk_level = summary["risk_level"]
    item.risk_score = summary["risk_score"]
    item.subjects_tracked = summary["subjects_tracked"]
    item.lowest_standing = summary["lowest_standing"]
    item.average_standing = summary["average_standing"]
    item.subjects_flagged = summary["subjects_flagged"]
    graded = [r["term_grade"] for r in pace_rows if r.get("term_grade") is not None]
    item.lowest_term_grade = min(graded) if graded else None
    item.weakest_subject = summary["weakest_subject"]
    item.required_in_weakest = summary["required_in_weakest"]
    item.terms_done = summary["terms_done"]
    weakest = max(pace_rows, key=lambda r: r["risk_score"]) if pace_rows else None
    if weakest is not None:
        item.weakest_standing = weakest.get("standing")
        item.weakest_basis = weakest.get("basis")
        item.needs_to_pass = weakest.get("needs_to_pass")
    return item


async def term_grades_by_subject(
    db: AsyncSession, *, school_id: int, student_ids=None
) -> Dict[tuple, list]:
    """Every term grade a learner has in each subject, keyed by enrolment.

    The final grade is the mean of the terms, so judging a learner means
    looking at all their terms at once rather than at the one being
    encoded. Loaded in a single query for the whole page.
    """
    stmt = select(StudentPace).where(StudentPace.school_id == school_id)
    if student_ids is not None:
        ids = list(student_ids)
        if not ids:
            return {}
        stmt = stmt.where(StudentPace.student_id.in_(ids))
    out: Dict[tuple, list] = {}
    for row in (await db.execute(stmt)).scalars():
        out.setdefault((row.enrollment_id, row.subject), []).append(
            (row.grading_period or 1, row.transmuted_grade)
        )
    # Ordered by term, so the mean and the "needs this much from here" both
    # read the terms in the order they happened.
    return {
        key: [grade for _period, grade in sorted(periods)]
        for key, periods in out.items()
    }


async def school_passing_mark(db: AsyncSession, school_id: int) -> float:
    """The school's own passing mark, so the risk bands follow its policy."""
    from app.models.schools import School

    value = (
        await db.execute(
            select(School.passing_mark).where(School.school_id == school_id)
        )
    ).scalar_one_or_none()
    return float(value) if value else 75.0


async def build_student_list(
    db: AsyncSession,
    students: List[Student],
    *,
    school_id: int,
    visible: Optional[Callable[[Optional[int], Optional[str]], bool]] = None,
) -> List[StudentResponse]:
    from app.models.schools import School

    ids = [s.student_id for s in students]
    lookups = await Lookups.load(db, school_id=school_id)
    enrollments = await current_enrollments(db, ids, school_id=school_id)
    mark = await school_passing_mark(db, school_id)
    terms = (
        await db.execute(
            select(School.grading_periods).where(School.school_id == school_id)
        )
    ).scalar_one_or_none()
    paces = await paces_by_student(
        db, ids, school_id=school_id, passing_mark=mark, terms_per_year=terms,
        visible=visible,
    )
    predictions = await latest_prediction_per_student(
        db, ids, school_id=school_id, visible=visible
    )

    out = []
    for student in students:
        item = build_student_response(
            student, enrollments.get(student.student_id),
            paces.get(student.student_id, []), lookups,
        )
        # The subject the final-grade model is most worried about.
        prediction = predictions.get(student.student_id)
        if prediction is not None:
            item.ai_subject = prediction.subject
            item.ai_predicted_final = prediction.predicted_final
            item.ai_range_low = prediction.range_low
            item.ai_range_high = prediction.range_high
            item.ai_risk_level = prediction.risk_level
            item.ai_risk_score = prediction.risk_score
            item.ai_used_threshold = prediction.used_threshold
            item.ai_source = prediction.source
            item.ai_model_version = prediction.model_version
        out.append(item)
    return out


def is_year_prediction(row) -> bool:
    """Was this prediction made by the final-grade model (or its fallback)?

    That model reads a learner's first term and predicts their final grade
    in the subject. Rows from earlier models answered different questions —
    "will THIS term end below 80", "will the rest of the year average below
    80" — and stay in the table as history, but they are not a current answer
    to the question asked now. They are told apart by their stored inputs,
    the one thing that cannot be mislabelled: only the final-grade model
    reads ``q1_term_grade``.
    """
    return '"q1_term_grade"' in (row.features or "")


def newest_year_predictions(rows) -> Dict[tuple, "AIRiskPrediction"]:
    """The current prediction per learner and subject.

    ``rows`` must be ordered by ``prediction_id`` ascending; the table is
    append-only, so the last one per key is the current one. Keyed by
    ``(student_id, enrollment_id, subject)`` because one year-model answer
    covers every term of that subject.
    """
    out: Dict[tuple, AIRiskPrediction] = {}
    for row in rows:
        if is_year_prediction(row):
            out[(row.student_id, row.enrollment_id, row.subject)] = row
    return out


async def latest_predictions(
    db: AsyncSession,
    enrollment_ids: Optional[Iterable[int]] = None,
    *,
    school_id: int,
) -> Dict[tuple, "AIRiskPrediction"]:
    """The current year-model prediction per enrolment and subject, keyed by
    ``(enrollment_id, subject)``, so every term of a subject shows it.

    Done in Python rather than with a window function so the same query works
    on SQLite and PostgreSQL.
    """
    stmt = (
        select(AIRiskPrediction)
        .where(AIRiskPrediction.school_id == school_id)
        .order_by(AIRiskPrediction.prediction_id)
    )
    if enrollment_ids is not None:
        ids = list({int(i) for i in enrollment_ids if i is not None})
        if not ids:
            return {}
        stmt = stmt.where(AIRiskPrediction.enrollment_id.in_(ids))
    rows = (await db.execute(stmt)).scalars().all()
    return {
        (enrollment_id, subject): row
        for (_, enrollment_id, subject), row in newest_year_predictions(rows).items()
    }


async def latest_prediction_per_student(
    db: AsyncSession,
    student_ids: Optional[Iterable[int]] = None,
    *,
    school_id: int,
    visible: Optional[Callable[[Optional[int], Optional[str]], bool]] = None,
) -> Dict[int, "AIRiskPrediction"]:
    """The student's worst current prediction across their subjects.
    ``visible`` keeps only the subjects the viewer may see.

    A learner flagged HIGH in one subject is a concern even when the other
    subjects are fine, so the student-level view takes the highest risk score
    among that student's newest per-subject predictions — the same "worst
    subject wins" rule the rule-based summary uses.
    """
    stmt = (
        select(AIRiskPrediction)
        .where(AIRiskPrediction.school_id == school_id)
        .order_by(AIRiskPrediction.prediction_id)
    )
    if student_ids is not None:
        ids = [int(i) for i in student_ids]
        if not ids:
            return {}
        stmt = stmt.where(AIRiskPrediction.student_id.in_(ids))
    rows = (await db.execute(stmt)).scalars().all()
    if visible is not None:
        rows = [r for r in rows if visible(r.enrollment_id, r.subject)]

    worst: Dict[int, AIRiskPrediction] = {}
    for row in newest_year_predictions(rows).values():
        current = worst.get(row.student_id)
        if current is None or row.risk_score > current.risk_score:
            worst[row.student_id] = row
    return worst


async def sections_for_teacher(db: AsyncSession, teacher_id, *, school_id: int) -> List[int]:
    rows = (
        await db.execute(
            select(TeacherAssignment.section_id).where(
                TeacherAssignment.school_id == school_id,
                TeacherAssignment.teacher_id == teacher_id,
            )
        )
    ).scalars().all()
    return list({int(r) for r in rows})
