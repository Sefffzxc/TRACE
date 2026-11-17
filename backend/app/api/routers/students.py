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
from app.services.access import class_access
from app.services.audit import Action, log_event
from app.services.school_years import resolve_school_year_id, require_current_year_record
from app.services.roster_access import lock_roster_section, require_roster_section
from app.services.enrichment import (
    school_passing_mark,
    Lookups,
    build_student_list,
    build_student_response,
    current_enrollments,
    latest_predictions,
    paces_by_student,
    sections_for_teacher,
    enrollment_ids_for_year,
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
    year_id = await resolve_school_year_id(db, school_id, given, required=True)
    await require_current_year_record(db, school_id, year_id)
    return year_id


# ══════════════════════════════════════════════════════════════════════════════
# Students
# ══════════════════════════════════════════════════════════════════════════════
async def _student_access(db: AsyncSession, current: Staff, scope: int, student_id: int):
    """The viewer's class access, refusing a learner outside their sections.

    Answers 404 rather than 403 so a teacher cannot tell which learners exist
    in sections they do not handle.
    """
    access = await class_access(db, current, scope)
    enrollment = (await current_enrollments(db, [student_id], school_id=scope)).get(student_id)
    if enrollment is None or not access.can_see_section(enrollment.section_id):
        raise HTTPException(status_code=404, detail="Student not found in the current school year.")
    return access


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
    school_year_id = await resolve_school_year_id(db, scope)
    stmt = scoped(
        select(Student).where(Student.student_id.in_(
            select(StudentEnrollment.student_id).where(
                StudentEnrollment.school_id == scope,
                StudentEnrollment.school_year_id == (school_year_id or 0),
                StudentEnrollment.is_active.is_(True),
            )
        )).order_by(Student.last_name, Student.first_name), Student, scope
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
    # A teacher sees the learners in the sections they teach or advise, each
    # summarised from the subjects they may see (app/services/access.py).
    access = await class_access(db, current, scope)
    rows = await build_student_list(
        db, list(students), school_id=scope,
        visible=None if access.unrestricted else access.can_see_record,
    )
    rows = [r for r in rows if access.can_see_section(r.section_id)]

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


@router.get("/api/students/returning")
async def returning_students(
    section_id: int,
    search: str = Query(..., min_length=2, max_length=100),
    current: Staff = Depends(get_current_user),
    scope: int = Depends(school_scope),
    db: AsyncSession = Depends(get_db),
):
    """Find a retained school profile to enroll again, without prior scores.

    The teacher must have a roster assignment in the destination this year.
    This is separate from the current-year roster, which stays empty until
    enrollment; searching does not copy any academic data.
    """
    school_year_id = await resolve_school_year_id(db, scope, required=True)
    await require_roster_section(db, current, scope, section_id)
    already_enrolled = select(StudentEnrollment.student_id).where(
        StudentEnrollment.school_id == scope,
        StudentEnrollment.school_year_id == school_year_id,
        StudentEnrollment.is_active.is_(True),
    )
    pattern = f"%{search.strip().lower()}%"
    if len(search.strip()) < 2:
        return []
    rows = (await db.scalars(select(Student).where(
        Student.school_id == scope,
        Student.is_active.is_(True),
        Student.student_id.not_in(already_enrolled),
        or_(func.lower(Student.first_name).like(pattern),
            func.lower(Student.last_name).like(pattern),
            func.lower(func.coalesce(Student.login_id, "")).like(pattern),
            func.lower(Student.first_name + " " + Student.last_name).like(pattern)),
    ).order_by(Student.last_name, Student.first_name).limit(50))).all()
    return [{
        "student_id": row.student_id, "login_id": row.login_id,
        "first_name": row.first_name, "middle_name": row.middle_name,
        "last_name": row.last_name, "full_name": row.full_name, "gender": row.gender,
    } for row in rows]


@router.get("/api/students/{student_id}", response_model=StudentResponse)
async def read_student(
    student_id: int,
    current: Staff = Depends(get_current_user),
    scope: int = Depends(school_scope),
    db: AsyncSession = Depends(get_db),
):
    student = await get_scoped(db, Student, student_id, scope, "Student")
    access = await _student_access(db, current, scope, student_id)
    lookups = await Lookups.load(db, school_id=scope)
    enrollments = await current_enrollments(db, [student_id], school_id=scope)
    paces = await paces_by_student(db, [student_id], school_id=scope,
                                   passing_mark=await school_passing_mark(db, scope),
                                   visible=None if access.unrestricted else access.can_see_record)
    return build_student_response(
        student, enrollments.get(student_id), paces.get(student_id, []), lookups
    )


@router.get("/api/students/{student_id}/profile")
async def student_profile(
    student_id: int,
    current: Staff = Depends(get_current_user),
    scope: int = Depends(school_scope),
    db: AsyncSession = Depends(get_db),
):
    """Everything the profile screen shows, in one request."""
    passing_mark = await school_passing_mark(db, scope)
    student = await get_scoped(db, Student, student_id, scope, "Student")
    access = await _student_access(db, current, scope, student_id)
    visible = None if access.unrestricted else access.can_see_record

    lookups = await Lookups.load(db, school_id=scope)
    enrollments_map = await current_enrollments(db, [student_id], school_id=scope)
    paces_map = await paces_by_student(
        db, [student_id], school_id=scope, passing_mark=passing_mark, visible=visible
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
                StudentPace.enrollment_id.in_(enrollment_ids_for_year(
                    scope, await resolve_school_year_id(db, scope)
                )),
            )
            .order_by(StudentPace.subject)
        )
    ).scalars().all()
    if visible is not None:
        pace_rows = [r for r in pace_rows if visible(r.enrollment_id, r.subject)]

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
    # Validate everything before adding a Student: omitting enrolment fields
    # must not bypass the section rule and create an orphan learner.
    section = await require_roster_section(db, current, scope, section_id, grade_level_id)
    resolved_year = await _resolve_school_year(db, school_year_id, scope)
    await lock_roster_section(db, scope, section.section_id)
    section = await require_roster_section(db, current, scope, section_id, grade_level_id)

    student = Student(**data, school_id=scope, created_by=current.id)
    db.add(student)
    await db.flush()
    student.login_id = f"S{student.student_id:04d}"

    db.add(
        StudentEnrollment(
            school_id=scope,
            student_id=student.student_id,
            grade_level_id=section.grade_level_id,
            section_id=section.section_id,
            school_year_id=resolved_year,
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
    await resolve_school_year_id(db, scope, required=True)
    await _student_access(db, current, scope, student_id)
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
    current: Staff = Depends(get_current_user),
    scope: int = Depends(school_scope),
    db: AsyncSession = Depends(get_db),
):
    """Remove a learner from this year's roster while retaining past records."""
    student = await get_scoped(db, Student, student_id, scope, "Student")
    await resolve_school_year_id(db, scope, required=True)
    enrollment = (
        await current_enrollments(db, [student_id], school_id=scope)
    ).get(student_id)
    if enrollment is None:
        raise HTTPException(status_code=404, detail="Student not found in the current school year.")
    if current.role != ROLE_ADMIN:
        enrollment = (
            await current_enrollments(db, [student_id], school_id=scope)
        ).get(student_id)
        mine = set(await sections_for_teacher(db, current.id, school_id=scope))
        if enrollment is None or enrollment.section_id not in mine:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You can only remove learners enrolled in a section you handle.",
            )
    await log_event(
        db, Action.STUDENT_DELETED, actor_id=current.id, detail=student.full_name,
        request=request,
    )
    enrollment.is_active = False
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
    current: Staff = Depends(get_current_user),
    scope: int = Depends(school_scope),
    db: AsyncSession = Depends(get_db),
):
    school_year_id = await resolve_school_year_id(db, scope, school_year_id)
    access = await class_access(db, current, scope, school_year_id)
    stmt = scoped(select(StudentEnrollment).where(
        StudentEnrollment.school_year_id == (school_year_id or 0)
    ), StudentEnrollment, scope)
    if not access.unrestricted:
        stmt = stmt.where(StudentEnrollment.section_id.in_(access.sections or {0}))
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
    await _resolve_school_year(db, payload.school_year_id, scope)
    section = await require_roster_section(
        db, current, scope, payload.section_id, payload.grade_level_id,
    )
    await get_scoped(db, Student, payload.student_id, scope, "Student")
    if await get_scoped_optional(db, SchoolYear, payload.school_year_id, scope) is None:
        raise HTTPException(status_code=400, detail="That school year does not exist.")
    section = await lock_roster_section(db, scope, section.section_id)
    section = await require_roster_section(
        db, current, scope, payload.section_id, payload.grade_level_id,
    )

    duplicate = (
        await db.execute(
            select(StudentEnrollment).where(
                StudentEnrollment.school_id == scope,
                StudentEnrollment.student_id == payload.student_id,
                StudentEnrollment.school_year_id == payload.school_year_id,
            )
        )
    ).scalar_one_or_none()
    if duplicate and (duplicate.is_active or duplicate.section_id != payload.section_id):
        raise HTTPException(
            status_code=409,
            detail="That student is already enrolled for this school year.",
        )

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

    if duplicate is not None:
        duplicate.is_active = True
        await db.commit()
        await db.refresh(duplicate)
        return EnrollmentResponse.model_validate(duplicate)

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
    current: Staff = Depends(get_current_user),
    scope: int = Depends(school_scope),
    db: AsyncSession = Depends(get_db),
):
    row = await get_scoped(db, StudentEnrollment, enrollment_id, scope, "Enrolment")
    await require_current_year_record(db, scope, row.school_year_id)
    data = payload.model_dump(exclude_unset=True)
    if "school_year_id" in data and data["school_year_id"] != row.school_year_id:
        raise HTTPException(status_code=409, detail="Enroll the existing student for the new year instead of moving an old enrollment.")
    access = await class_access(db, current, scope)
    access.require_section(row.section_id)
    if data.get("section_id") is not None:
        access.require_section(data["section_id"])

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
    await require_current_year_record(db, scope, row.school_year_id)
    row.is_active = False
    await db.commit()
