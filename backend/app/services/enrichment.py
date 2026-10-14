"""Read-side helpers that flatten the relational model into the shapes the
React screens consume, so the UI never has to stitch six lists together."""
from __future__ import annotations

from typing import Dict, Iterable, List, Optional

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
) -> Dict[int, List[dict]]:
    stmt = select(StudentPace).where(StudentPace.school_id == school_id)
    if student_ids is not None:
        ids = list(student_ids)
        if not ids:
            return {}
        stmt = stmt.where(StudentPace.student_id.in_(ids))
    rows = (await db.execute(stmt)).scalars().all()

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
                "reachable": outlook.get("reachable", True),
                "intervention": evaluation.get("intervention"),
            }
        )
    return out


def build_student_response(
    student: Student,
    enrollment: Optional[StudentEnrollment],
    pace_rows: List[dict],
    lookups: Lookups,
) -> StudentResponse:
    item = StudentResponse.model_validate(student)
    item.full_name = student.full_name

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
    db: AsyncSession, students: List[Student], *, school_id: int
) -> List[StudentResponse]:
    ids = [s.student_id for s in students]
    lookups = await Lookups.load(db, school_id=school_id)
    enrollments = await current_enrollments(db, ids, school_id=school_id)
    mark = await school_passing_mark(db, school_id)
    paces = await paces_by_student(db, ids, school_id=school_id, passing_mark=mark)
    return [
        build_student_response(
            student, enrollments.get(student.student_id), paces.get(student.student_id, []), lookups
        )
        for student in students
    ]


async def latest_predictions(
    db: AsyncSession,
    pace_ids: Optional[Iterable[int]] = None,
    *,
    school_id: int,
) -> Dict[int, "AIRiskPrediction"]:
    """The newest AI prediction per PACE record, keyed by ``pace_id``.

    ``ai_risk_predictions`` is append-only, so "current" means "the most
    recently created row for this PACE record". Done in Python rather than with
    a window function so the same query works on SQLite and PostgreSQL.
    """
    stmt = (
        select(AIRiskPrediction)
        .where(AIRiskPrediction.school_id == school_id)
        .order_by(AIRiskPrediction.prediction_id)
    )
    if pace_ids is not None:
        ids = [int(i) for i in pace_ids if i is not None]
        if not ids:
            return {}
        stmt = stmt.where(AIRiskPrediction.pace_id.in_(ids))
    rows = (await db.execute(stmt)).scalars().all()

    out: Dict[int, AIRiskPrediction] = {}
    for row in rows:
        if row.pace_id is not None:
            out[row.pace_id] = row  # ordered ascending, so the last one wins
    return out


async def latest_prediction_per_student(
    db: AsyncSession,
    student_ids: Optional[Iterable[int]] = None,
    *,
    school_id: int,
) -> Dict[int, "AIRiskPrediction"]:
    """The student's worst current prediction across their subjects.

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

    newest: Dict[tuple, AIRiskPrediction] = {}
    for row in rows:
        newest[(row.student_id, row.pace_id, row.subject)] = row

    worst: Dict[int, AIRiskPrediction] = {}
    for row in newest.values():
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
