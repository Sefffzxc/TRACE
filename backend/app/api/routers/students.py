"""Student records and enrolment."""
from __future__ import annotations

from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_admin, get_current_user, school_scope
from app.api.scoping import get_scoped, get_scoped_optional, scoped
from app.db.session import get_db
from app.models.academic import GradeLevel, SchoolYear, Section
from app.models.staff import ROLE_ADMIN, Staff
from app.models.students import Student, StudentEnrollment, StudentPace
from app.schemas.students import (
    AIPredictionResponse,
    EnrollmentCreate,
    EnrollmentResponse,
    EnrollmentUpdate,
    StudentCreate,
    StudentResponse,
    StudentUpdate,
)
from app.services import risk
from app.services.audit import Action, log_event
from app.services.enrichment import (
    school_passing_mark,
    Lookups,
    build_student_list,
    build_student_response,
    current_enrollments,
    latest_predictions,
    paces_by_student,
    sections_for_teacher,
)

router = APIRouter(tags=["Students"])


async def _next_login_id(db: AsyncSession, school_id: int) -> str:
    highest = (
        await db.execute(
            scoped(select(func.max(Student.student_id)), Student, school_id)
        )
    ).scalar_one()
    return f"S{(highest or 0) + 1:04d}"


async def _resolve_school_year(
    db: AsyncSession, given: Optional[int], school_id: int
) -> int:
    if given:
        # A school year supplied by the caller must be one of this school's own.
        if await get_scoped_optional(db, SchoolYear, given, school_id) is None:
            raise HTTPException(
                status_code=400, detail="That school year does not exist."
            )
        return given
    row = (
        await db.execute(
            scoped(
                select(SchoolYear).where(SchoolYear.is_current.is_(True)),
                SchoolYear,
                school_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = (
            await db.execute(
                scoped(
                    select(SchoolYear).order_by(SchoolYear.start_date.desc()).limit(1),
                    SchoolYear,
                    school_id,
                )
            )
        ).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=400,
            detail="No school year has been set up yet. Create one under Class Management first.",
        )
    return row.school_year_id


# ══════════════════════════════════════════════════════════════════════════════
# Students
# ══════════════════════════════════════════════════════════════════════════════
@router.get("/api/students", response_model=List[StudentResponse])
async def list_students(
    search: Optional[str] = Query(default=None),
    grade_level_id: Optional[int] = None,
    section_id: Optional[int] = None,
    risk_level: Optional[str] = None,
    is_active: Optional[bool] = None,
    mine: bool = Query(default=False, description="Only students in my sections"),
    current: Staff = Depends(get_current_user),
    scope: int = Depends(school_scope),
    db: AsyncSession = Depends(get_db),
):
    stmt = scoped(
        select(Student).order_by(Student.last_name, Student.first_name), Student, scope
    )
    if is_active is not None:
        stmt = stmt.where(Student.is_active.is_(is_active))
    if search:
        pattern = f"%{search.lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(Student.first_name).like(pattern),
                func.lower(Student.last_name).like(pattern),
                func.lower(func.coalesce(Student.login_id, "")).like(pattern),
            )
        )

    students = (await db.execute(stmt)).scalars().all()
    rows = await build_student_list(db, list(students), school_id=scope)

    # Teachers may narrow the list to the sections they actually handle.
    if mine and current.role != ROLE_ADMIN:
        allowed = set(await sections_for_teacher(db, current.id, school_id=scope))
        rows = [r for r in rows if r.section_id in allowed]

    if grade_level_id:
        rows = [r for r in rows if r.grade_level_id == grade_level_id]
    if section_id:
        rows = [r for r in rows if r.section_id == section_id]
    if risk_level:
        wanted = {lvl.strip().lower() for lvl in risk_level.split(",")}
        rows = [r for r in rows if r.risk_level in wanted]
    return rows


@router.get("/api/students/{student_id}", response_model=StudentResponse)
async def read_student(
    student_id: int,
    _: Staff = Depends(get_current_user),
    scope: int = Depends(school_scope),
    db: AsyncSession = Depends(get_db),
):
    student = await get_scoped(db, Student, student_id, scope, "Student")
    lookups = await Lookups.load(db, school_id=scope)
    enrollments = await current_enrollments(db, [student_id], school_id=scope)
    paces = await paces_by_student(db, [student_id], school_id=scope,
                                   passing_mark=await school_passing_mark(db, scope))
    return build_student_response(
        student, enrollments.get(student_id), paces.get(student_id, []), lookups
    )


@router.get("/api/students/{student_id}/profile")
async def student_profile(
    student_id: int,
    _: Staff = Depends(get_current_user),
    scope: int = Depends(school_scope),
    db: AsyncSession = Depends(get_db),
):
    """Everything the profile screen shows, in one request."""
    passing_mark = await school_passing_mark(db, scope)
    student = await get_scoped(db, Student, student_id, scope, "Student")

    lookups = await Lookups.load(db, school_id=scope)
    enrollments_map = await current_enrollments(db, [student_id], school_id=scope)
    paces_map = await paces_by_student(
        db, [student_id], school_id=scope, passing_mark=passing_mark
    )
    overview = build_student_response(
        student, enrollments_map.get(student_id), paces_map.get(student_id, []), lookups
    )

    pace_rows = (
        await db.execute(
            select(StudentPace)
            .where(
                StudentPace.student_id == student_id,
                StudentPace.school_id == scope,
            )
            .order_by(StudentPace.subject)
        )
    ).scalars().all()

    # Newest AI prediction per PACE record, shown alongside — never instead of —
    # the school's own rule-based standing.
    predictions = await latest_predictions(
        db, [r.enrollment_id for r in pace_rows], school_id=scope
    )

    subjects = []
    for row in pace_rows:
        evaluation = risk.evaluate_row(row, passing_mark)
        prediction = predictions.get((row.enrollment_id, row.subject))
        subjects.append(
            {
                "pace_id": row.pace_id,
                "subject": row.subject,
                "current_pace": row.current_pace,
                "expected_pace": row.expected_pace,
                "total_paces": row.total_paces,
                "pace_percent": row.pace_percent,
                "paces_behind": row.paces_behind,
                "standing": evaluation["standing"],
                "basis": evaluation["basis"],
                "term_grade": row.transmuted_grade,
                "descriptor": row.descriptor,
                "average_score": row.average_score,
                "remarks": row.remarks,
                "updated_at": row.updated_at,
                # The class-record values behind this term, so the profile
                # shows what the readings were based on. The year model reads
                # these from the FIRST term; ``ai`` carries its exact inputs.
                "grading_period": row.grading_period,
                "absences": row.absences,
                "ww_percent": row.ww_percent,
                "pt_percent": row.pt_percent,
                "exam_percent": row.exam_percent,
                "extracurricular_count": row.extracurricular_count,
                "ai": (
                    AIPredictionResponse.model_validate(prediction).model_dump(mode="json")
                    if prediction is not None
                    else None
                ),
                **{
                    k: evaluation[k]
                    for k in ("risk_score", "risk_level", "status", "intervention")
                },
            }
        )

    history_rows = (
        await db.execute(
            select(StudentEnrollment)
            .where(
                StudentEnrollment.student_id == student_id,
                StudentEnrollment.school_id == scope,
            )
            .order_by(StudentEnrollment.school_year_id.desc())
        )
    ).scalars().all()
    years = {
        y.school_year_id: y.year
        for y in (
            await db.execute(scoped(select(SchoolYear), SchoolYear, scope))
        ).scalars()
    }
    history = [
        {
            "enrollment_id": row.enrollment_id,
            "school_year": years.get(row.school_year_id),
            "grade_level_name": lookups.grade_levels.get(row.grade_level_id),
            "section_name": lookups.sections.get(row.section_id),
            "is_active": row.is_active,
            "end_of_year_status": row.end_of_year_status,
        }
        for row in history_rows
    ]

    return {
        "student": overview.model_dump(mode="json"),
        "subjects": subjects,
        "enrollment_history": history,
    }


@router.post("/api/students", response_model=StudentResponse, status_code=201)
async def create_student(
    payload: StudentCreate,
    request: Request,
    current: Staff = Depends(get_current_user),
    scope: int = Depends(school_scope),
    db: AsyncSession = Depends(get_db),
):
    data = payload.model_dump()
    grade_level_id = data.pop("grade_level_id", None)
    section_id = data.pop("section_id", None)
    school_year_id = data.pop("school_year_id", None)

    student = Student(**data, school_id=scope, created_by=current.id)
    db.add(student)
    await db.flush()
    student.login_id = f"S{student.student_id:04d}"

    if grade_level_id and section_id:
        if await get_scoped_optional(db, GradeLevel, grade_level_id, scope) is None:
            raise HTTPException(status_code=400, detail="That grade level does not exist.")
        if await get_scoped_optional(db, Section, section_id, scope) is None:
            raise HTTPException(status_code=400, detail="That section does not exist.")
        db.add(
            StudentEnrollment(
                school_id=scope,
                student_id=student.student_id,
                grade_level_id=grade_level_id,
                section_id=section_id,
                school_year_id=await _resolve_school_year(db, school_year_id, scope),
                enrolled_by=current.id,
                enrollment_date=date.today(),
                is_active=True,
            )
        )

    await log_event(
        db,
        Action.STUDENT_CREATED,
        actor_id=current.id,
        detail=f"{student.full_name} ({student.login_id})",
        request=request,
    )
    await db.commit()
    await db.refresh(student)

    lookups = await Lookups.load(db, school_id=scope)
    enrollments = await current_enrollments(db, [student.student_id], school_id=scope)
    return build_student_response(
        student, enrollments.get(student.student_id), [], lookups
    )


@router.patch("/api/students/{student_id}", response_model=StudentResponse)
async def update_student(
    student_id: int,
    payload: StudentUpdate,
    request: Request,
    current: Staff = Depends(get_current_user),
    scope: int = Depends(school_scope),
    db: AsyncSession = Depends(get_db),
):
    student = await get_scoped(db, Student, student_id, scope, "Student")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(student, field, value)
    await log_event(
        db, Action.STUDENT_UPDATED, actor_id=current.id, detail=student.full_name,
        request=request,
    )
    await db.commit()
    await db.refresh(student)

    lookups = await Lookups.load(db, school_id=scope)
    enrollments = await current_enrollments(db, [student_id], school_id=scope)
    paces = await paces_by_student(db, [student_id], school_id=scope,
                                   passing_mark=await school_passing_mark(db, scope))
    return build_student_response(
        student, enrollments.get(student_id), paces.get(student_id, []), lookups
    )


@router.delete("/api/students/{student_id}", status_code=204)
async def delete_student(
    student_id: int,
    request: Request,
    admin: Staff = Depends(get_current_admin),
    scope: int = Depends(school_scope),
    db: AsyncSession = Depends(get_db),
):
    student = await get_scoped(db, Student, student_id, scope, "Student")
    await log_event(
        db, Action.STUDENT_DELETED, actor_id=admin.id, detail=student.full_name,
        request=request,
    )
    await db.delete(student)
    await db.commit()


# ══════════════════════════════════════════════════════════════════════════════
# Enrolment
# ══════════════════════════════════════════════════════════════════════════════
@router.get("/api/enrollments", response_model=List[EnrollmentResponse])
async def list_enrollments(
    school_year_id: Optional[int] = None,
    section_id: Optional[int] = None,
    student_id: Optional[int] = None,
    is_active: Optional[bool] = None,
    _: Staff = Depends(get_current_user),
    scope: int = Depends(school_scope),
    db: AsyncSession = Depends(get_db),
):
    stmt = scoped(select(StudentEnrollment), StudentEnrollment, scope)
    if school_year_id:
        stmt = stmt.where(StudentEnrollment.school_year_id == school_year_id)
    if section_id:
        stmt = stmt.where(StudentEnrollment.section_id == section_id)
    if student_id:
        stmt = stmt.where(StudentEnrollment.student_id == student_id)
    if is_active is not None:
        stmt = stmt.where(StudentEnrollment.is_active.is_(is_active))
    rows = (await db.execute(stmt)).scalars().all()

    lookups = await Lookups.load(db, school_id=scope)
    students = {
        s.student_id: s.full_name
        for s in (await db.execute(scoped(select(Student), Student, scope))).scalars()
    }
    years = {
        y.school_year_id: y.year
        for y in (
            await db.execute(scoped(select(SchoolYear), SchoolYear, scope))
        ).scalars()
    }

    out = []
    for row in rows:
        item = EnrollmentResponse.model_validate(row)
        item.student_name = students.get(row.student_id)
        item.grade_level_name = lookups.grade_levels.get(row.grade_level_id)
        item.section_name = lookups.sections.get(row.section_id)
        item.school_year_name = years.get(row.school_year_id)
        out.append(item)
    return out


@router.post("/api/enrollments", response_model=EnrollmentResponse, status_code=201)
async def create_enrollment(
    payload: EnrollmentCreate,
    current: Staff = Depends(get_current_user),
    scope: int = Depends(school_scope),
    db: AsyncSession = Depends(get_db),
):
    await get_scoped(db, Student, payload.student_id, scope, "Student")

    if await get_scoped_optional(db, GradeLevel, payload.grade_level_id, scope) is None:
        raise HTTPException(status_code=400, detail="That grade level does not exist.")
    if await get_scoped_optional(db, SchoolYear, payload.school_year_id, scope) is None:
        raise HTTPException(status_code=400, detail="That school year does not exist.")

    duplicate = (
        await db.execute(
            select(StudentEnrollment).where(
                StudentEnrollment.school_id == scope,
                StudentEnrollment.student_id == payload.student_id,
                StudentEnrollment.school_year_id == payload.school_year_id,
            )
        )
    ).scalar_one_or_none()
    if duplicate:
        raise HTTPException(
            status_code=409,
            detail="That student is already enrolled for this school year.",
        )

    section = await get_scoped_optional(db, Section, payload.section_id, scope)
    if section is None:
        raise HTTPException(status_code=400, detail="That section does not exist.")

    taken = (
        await db.execute(
            select(func.count())
            .select_from(StudentEnrollment)
            .where(
                StudentEnrollment.school_id == scope,
                StudentEnrollment.section_id == payload.section_id,
                StudentEnrollment.school_year_id == payload.school_year_id,
                StudentEnrollment.is_active.is_(True),
            )
        )
    ).scalar_one()
    if taken >= section.capacity:
        raise HTTPException(
            status_code=409,
            detail=f"{section.name} is full ({taken}/{section.capacity}).",
        )

    row = StudentEnrollment(
        **payload.model_dump(),
        school_id=scope,
        enrolled_by=current.id,
    )
    if row.enrollment_date is None:
        row.enrollment_date = date.today()
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return EnrollmentResponse.model_validate(row)


@router.patch("/api/enrollments/{enrollment_id}", response_model=EnrollmentResponse)
async def update_enrollment(
    enrollment_id: int,
    payload: EnrollmentUpdate,
    _: Staff = Depends(get_current_user),
    scope: int = Depends(school_scope),
    db: AsyncSession = Depends(get_db),
):
    row = await get_scoped(db, StudentEnrollment, enrollment_id, scope, "Enrolment")
    data = payload.model_dump(exclude_unset=True)

    # Anything the payload may point at has to belong to this school, or the
    # loop below would quietly move the enrolment onto another school's rows.
    for field in ("grade_level_id", "next_grade_level_id"):
        if data.get(field) is not None and (
            await get_scoped_optional(db, GradeLevel, data[field], scope) is None
        ):
            raise HTTPException(
                status_code=400, detail="That grade level does not exist."
            )
    if data.get("section_id") is not None and (
        await get_scoped_optional(db, Section, data["section_id"], scope) is None
    ):
        raise HTTPException(status_code=400, detail="That section does not exist.")
    if data.get("school_year_id") is not None and (
        await get_scoped_optional(db, SchoolYear, data["school_year_id"], scope) is None
    ):
        raise HTTPException(status_code=400, detail="That school year does not exist.")

    for field, value in data.items():
        setattr(row, field, value)
    await db.commit()
    await db.refresh(row)
    return EnrollmentResponse.model_validate(row)


@router.delete("/api/enrollments/{enrollment_id}", status_code=204)
async def delete_enrollment(
    enrollment_id: int,
    _: Staff = Depends(get_current_admin),
    scope: int = Depends(school_scope),
    db: AsyncSession = Depends(get_db),
):
    row = await get_scoped(db, StudentEnrollment, enrollment_id, scope, "Enrolment")
    await db.delete(row)
    await db.commit()
