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


class Lookups:
    """One round-trip for every reference table the list views need."""

    def __init__(self) -> None:
        self.grade_levels: Dict[int, str] = {}
        self.sections: Dict[int, str] = {}
        self.section_grade: Dict[int, int] = {}
        self.advisers: Dict[int, str] = {}
        self.staff: Dict[str, str] = {}

    @classmethod
    async def load(cls, db: AsyncSession) -> "Lookups":
        self = cls()
        for level in (await db.execute(select(GradeLevel))).scalars():
            self.grade_levels[level.grade_level_id] = level.name
        for section in (await db.execute(select(Section))).scalars():
            self.sections[section.section_id] = section.name
            self.section_grade[section.section_id] = section.grade_level_id
        for person in (await db.execute(select(Staff))).scalars():
            self.staff[str(person.id)] = person.full_name
        rows = (
            await db.execute(
                select(TeacherAssignment).where(TeacherAssignment.is_adviser.is_(True))
            )
        ).scalars().all()
        for assignment in rows:
            self.advisers[assignment.section_id] = self.staff.get(
                str(assignment.teacher_id), ""
            )
        return self


async def current_enrollments(
    db: AsyncSession, student_ids: Optional[Iterable[int]] = None
) -> Dict[int, StudentEnrollment]:
    """The active enrolment per student, keyed by student id."""
    stmt = select(StudentEnrollment).where(StudentEnrollment.is_active.is_(True))
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
    db: AsyncSession, student_ids: Optional[Iterable[int]] = None
) -> Dict[int, List[dict]]:
    stmt = select(StudentPace)
    if student_ids is not None:
        ids = list(student_ids)
        if not ids:
            return {}
        stmt = stmt.where(StudentPace.student_id.in_(ids))
    rows = (await db.execute(stmt)).scalars().all()

    out: Dict[int, List[dict]] = {}
    for row in rows:
        score = risk.risk_score(
            row.paces_behind, row.pace_percent, row.attendance, row.total_paces
        )
        out.setdefault(row.student_id, []).append(
            {
                "subject": row.subject,
                "pace_percent": row.pace_percent,
                "paces_behind": row.paces_behind,
                "attendance": row.attendance,
                "risk_score": score,
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
    item.attendance = summary["attendance"]
    item.risk_level = summary["risk_level"]
    item.risk_score = summary["risk_score"]
    item.subjects_tracked = summary["subjects_tracked"]
    return item


async def build_student_list(db: AsyncSession, students: List[Student]) -> List[StudentResponse]:
    ids = [s.student_id for s in students]
    lookups = await Lookups.load(db)
    enrollments = await current_enrollments(db, ids)
    paces = await paces_by_student(db, ids)
    return [
        build_student_response(
            student, enrollments.get(student.student_id), paces.get(student.student_id, []), lookups
        )
        for student in students
    ]


async def latest_predictions(
    db: AsyncSession, pace_ids: Optional[Iterable[int]] = None
) -> Dict[int, "AIRiskPrediction"]:
    """The newest AI prediction per PACE record, keyed by ``pace_id``.

    ``ai_risk_predictions`` is append-only, so "current" means "the most
    recently created row for this PACE record". Done in Python rather than with
    a window function so the same query works on SQLite and PostgreSQL.
    """
    stmt = select(AIRiskPrediction).order_by(AIRiskPrediction.prediction_id)
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
    db: AsyncSession, student_ids: Optional[Iterable[int]] = None
) -> Dict[int, "AIRiskPrediction"]:
    """The student's worst current prediction across their subjects.

    A learner flagged HIGH in one subject is a concern even when the other
    subjects are fine, so the student-level view takes the highest risk score
    among that student's newest per-subject predictions — the same "worst
    subject wins" rule the rule-based summary uses.
    """
    stmt = select(AIRiskPrediction).order_by(AIRiskPrediction.prediction_id)
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


async def sections_for_teacher(db: AsyncSession, teacher_id) -> List[int]:
    rows = (
        await db.execute(
            select(TeacherAssignment.section_id).where(
                TeacherAssignment.teacher_id == teacher_id
            )
        )
    ).scalars().all()
    return list({int(r) for r in rows})
