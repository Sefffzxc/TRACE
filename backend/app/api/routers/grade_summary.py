"""A teacher's recorded-grade summary, read without requesting AI inference."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, school_scope
from app.api.scoping import get_scoped, scoped
from app.db.session import get_db
from app.models.academic import Section, Subject
from app.models.schools import School
from app.models.staff import ROLE_TEACHER, Staff
from app.models.students import Student, StudentEnrollment, StudentPace
from app.schemas.grade_summary import GradeSummaryResponse, GradeSummaryRow
from app.schemas.students import AIPredictionResponse
from app.services.access import class_access
from app.services.enrichment import latest_predictions
from app.services.grade_summary import summarise_grades
from app.services.school_years import resolve_school_year_id

router = APIRouter(tags=["Grade summary"])


@router.get("/api/grade-summary", response_model=GradeSummaryResponse)
async def grade_summary(
    section_id: int = Query(..., gt=0),
    subject_id: int = Query(..., gt=0),
    school_year_id: int | None = Query(default=None, gt=0),
    current: Staff = Depends(get_current_user),
    scope: int = Depends(school_scope),
    db: AsyncSession = Depends(get_db),
):
    if current.role != ROLE_TEACHER:
        raise HTTPException(status_code=403, detail="Grade summaries are available to teachers.")
    school = await get_scoped(db, School, scope, scope, "School")
    if school.tracks_units:
        raise HTTPException(status_code=400, detail="This school tracks learning units instead of class-record grades.")
    section = await get_scoped(db, Section, section_id, scope, "Section")
    subject = await get_scoped(db, Subject, subject_id, scope, "Subject")
    if subject.grade_level_id != section.grade_level_id:
        raise HTTPException(status_code=400, detail="That subject is not taught in this section's grade level.")
    year_id = await resolve_school_year_id(db, scope, school_year_id, required=True)
    access = await class_access(db, current, scope, year_id)
    if not access.can_see(section_id, subject.subject_name):
        raise HTTPException(status_code=403, detail="You are not assigned to this section and subject.")

    roster = (await db.execute(select(StudentEnrollment, Student).join(
        Student, Student.student_id == StudentEnrollment.student_id
    ).where(
        StudentEnrollment.school_id == scope, Student.school_id == scope,
        StudentEnrollment.school_year_id == year_id,
        StudentEnrollment.section_id == section_id, StudentEnrollment.is_active.is_(True),
    ).order_by(Student.last_name, Student.first_name, Student.student_id))).all()
    ids = [enrollment.enrollment_id for enrollment, _ in roster]
    records = (await db.scalars(scoped(select(StudentPace).where(
        StudentPace.enrollment_id.in_(ids), StudentPace.subject == subject.subject_name,
    ).order_by(StudentPace.pace_id), StudentPace, scope))).all() if ids else []
    by_enrollment = {}
    for row in records:
        by_enrollment.setdefault(row.enrollment_id, []).append(row)
    predictions = await latest_predictions(db, ids, school_id=scope, school_year_id=year_id)
    period_count = max(1, min(6, school.grading_periods or 3))
    passing_mark = float(school.passing_mark if school.passing_mark is not None else 75)
    rows = []
    for enrollment, student in roster:
        figures = summarise_grades(by_enrollment.get(enrollment.enrollment_id, []), period_count, passing_mark)
        prediction = predictions.get((enrollment.enrollment_id, subject.subject_name))
        rows.append(GradeSummaryRow(
            student_id=student.student_id, enrollment_id=enrollment.enrollment_id,
            student_name=student.full_name, login_id=student.login_id,
            ai=AIPredictionResponse.model_validate(prediction)
               if prediction is not None and figures["completed_periods"] else None,
            **figures,
        ))
    return GradeSummaryResponse(
        school_year_id=year_id, section_id=section_id, section_name=section.name,
        subject_id=subject_id, subject=subject.subject_name, period_count=period_count,
        period_label=school.grading_period_label or "Period", passing_mark=passing_mark, rows=rows,
    )
