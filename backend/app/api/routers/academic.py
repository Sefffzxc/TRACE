"""Class management: school years, grade levels, sections, subjects,
teacher assignments, the weekly timetable and teacher availability."""
from __future__ import annotations

import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_admin, get_current_user
from app.db.session import get_db
from app.models.academic import (
    ClassSchedule,
    GradeLevel,
    SchoolYear,
    Section,
    Subject,
    TeacherAssignment,
    TeacherAvailability,
)
from app.models.staff import Staff
from app.models.students import StudentEnrollment
from app.schemas.academic import (
    AvailabilityCreate,
    AvailabilityResponse,
    ClassScheduleCreate,
    ClassScheduleResponse,
    ClassScheduleUpdate,
    GradeLevelCreate,
    GradeLevelResponse,
    GradeLevelUpdate,
    SchoolYearCreate,
    SchoolYearResponse,
    SchoolYearUpdate,
    SectionCreate,
    SectionResponse,
    SectionUpdate,
    SubjectCreate,
    SubjectResponse,
    SubjectUpdate,
    TeacherAssignmentCreate,
    TeacherAssignmentResponse,
)

router = APIRouter(tags=["Class management"])


def _to_minutes(value: str) -> int:
    hour, minute = value.split(":")
    return int(hour) * 60 + int(minute)


def _overlaps(a_start: str, a_end: str, b_start: str, b_end: str) -> bool:
    return _to_minutes(a_start) < _to_minutes(b_end) and _to_minutes(b_start) < _to_minutes(a_end)


# ══════════════════════════════════════════════════════════════════════════════
# School years
# ══════════════════════════════════════════════════════════════════════════════
@router.get("/api/school-years", response_model=List[SchoolYearResponse])
async def list_school_years(
    _: Staff = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    rows = (
        await db.execute(select(SchoolYear).order_by(SchoolYear.start_date.desc()))
    ).scalars().all()
    return [SchoolYearResponse.model_validate(r) for r in rows]


@router.get("/api/school-years/current", response_model=Optional[SchoolYearResponse])
async def current_school_year(
    _: Staff = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    row = (
        await db.execute(select(SchoolYear).where(SchoolYear.is_current.is_(True)))
    ).scalar_one_or_none()
    if row is None:
        row = (
            await db.execute(
                select(SchoolYear).order_by(SchoolYear.start_date.desc()).limit(1)
            )
        ).scalar_one_or_none()
    return SchoolYearResponse.model_validate(row) if row else None


@router.post(
    "/api/school-years",
    response_model=SchoolYearResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_school_year(
    payload: SchoolYearCreate,
    _: Staff = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    if payload.end_date <= payload.start_date:
        raise HTTPException(
            status_code=400, detail="The end date must come after the start date."
        )
    exists = (
        await db.execute(select(SchoolYear).where(SchoolYear.year == payload.year))
    ).scalar_one_or_none()
    if exists:
        raise HTTPException(status_code=409, detail="That school year already exists.")

    row = SchoolYear(**payload.model_dump())
    if row.is_current:
        for other in (await db.execute(select(SchoolYear))).scalars().all():
            other.is_current = False
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return SchoolYearResponse.model_validate(row)


@router.patch("/api/school-years/{school_year_id}", response_model=SchoolYearResponse)
async def update_school_year(
    school_year_id: int,
    payload: SchoolYearUpdate,
    _: Staff = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    row = await db.get(SchoolYear, school_year_id)
    if row is None:
        raise HTTPException(status_code=404, detail="School year not found.")
    data = payload.model_dump(exclude_unset=True)
    if data.get("is_current"):
        for other in (await db.execute(select(SchoolYear))).scalars().all():
            other.is_current = False
    for field, value in data.items():
        setattr(row, field, value)
    await db.commit()
    await db.refresh(row)
    return SchoolYearResponse.model_validate(row)


@router.delete("/api/school-years/{school_year_id}", status_code=204)
async def delete_school_year(
    school_year_id: int,
    _: Staff = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    row = await db.get(SchoolYear, school_year_id)
    if row is None:
        raise HTTPException(status_code=404, detail="School year not found.")
    in_use = (
        await db.execute(
            select(func.count())
            .select_from(StudentEnrollment)
            .where(StudentEnrollment.school_year_id == school_year_id)
        )
    ).scalar_one()
    if in_use:
        raise HTTPException(
            status_code=409,
            detail=f"{in_use} enrolment(s) still reference this school year.",
        )
    await db.delete(row)
    await db.commit()


# ══════════════════════════════════════════════════════════════════════════════
# Grade levels
# ══════════════════════════════════════════════════════════════════════════════
@router.get("/api/grade-levels", response_model=List[GradeLevelResponse])
async def list_grade_levels(
    _: Staff = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    rows = (
        await db.execute(
            select(GradeLevel).order_by(GradeLevel.sort_order, GradeLevel.grade_level_id)
        )
    ).scalars().all()

    section_counts = dict(
        (
            await db.execute(
                select(Section.grade_level_id, func.count()).group_by(
                    Section.grade_level_id
                )
            )
        ).all()
    )
    student_counts = dict(
        (
            await db.execute(
                select(StudentEnrollment.grade_level_id, func.count())
                .where(StudentEnrollment.is_active.is_(True))
                .group_by(StudentEnrollment.grade_level_id)
            )
        ).all()
    )

    out = []
    for row in rows:
        item = GradeLevelResponse.model_validate(row)
        item.section_count = section_counts.get(row.grade_level_id, 0)
        item.student_count = student_counts.get(row.grade_level_id, 0)
        out.append(item)
    return out


@router.post(
    "/api/grade-levels", response_model=GradeLevelResponse, status_code=201
)
async def create_grade_level(
    payload: GradeLevelCreate,
    _: Staff = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    exists = (
        await db.execute(select(GradeLevel).where(GradeLevel.level == payload.level))
    ).scalar_one_or_none()
    if exists:
        raise HTTPException(status_code=409, detail="That grade level already exists.")
    row = GradeLevel(**payload.model_dump())
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return GradeLevelResponse.model_validate(row)


@router.patch("/api/grade-levels/{grade_level_id}", response_model=GradeLevelResponse)
async def update_grade_level(
    grade_level_id: int,
    payload: GradeLevelUpdate,
    _: Staff = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    row = await db.get(GradeLevel, grade_level_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Grade level not found.")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(row, field, value)
    await db.commit()
    await db.refresh(row)
    return GradeLevelResponse.model_validate(row)


@router.delete("/api/grade-levels/{grade_level_id}", status_code=204)
async def delete_grade_level(
    grade_level_id: int,
    _: Staff = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    row = await db.get(GradeLevel, grade_level_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Grade level not found.")
    enrolled = (
        await db.execute(
            select(func.count())
            .select_from(StudentEnrollment)
            .where(StudentEnrollment.grade_level_id == grade_level_id)
        )
    ).scalar_one()
    if enrolled:
        raise HTTPException(
            status_code=409,
            detail=f"{enrolled} student(s) are enrolled in this grade level.",
        )
    await db.delete(row)
    await db.commit()


# ══════════════════════════════════════════════════════════════════════════════
# Sections
# ══════════════════════════════════════════════════════════════════════════════
async def _decorate_sections(db: AsyncSession, rows) -> List[SectionResponse]:
    levels = {
        lvl.grade_level_id: lvl.name
        for lvl in (await db.execute(select(GradeLevel))).scalars().all()
    }
    counts = dict(
        (
            await db.execute(
                select(StudentEnrollment.section_id, func.count())
                .where(StudentEnrollment.is_active.is_(True))
                .group_by(StudentEnrollment.section_id)
            )
        ).all()
    )
    advisers = {}
    for assignment, staff in (
        await db.execute(
            select(TeacherAssignment, Staff)
            .join(Staff, Staff.id == TeacherAssignment.teacher_id)
            .where(TeacherAssignment.is_adviser.is_(True))
        )
    ).all():
        advisers[assignment.section_id] = staff.full_name

    out = []
    for row in rows:
        item = SectionResponse.model_validate(row)
        item.grade_level_name = levels.get(row.grade_level_id)
        item.enrolled_count = counts.get(row.section_id, 0)
        item.adviser_name = advisers.get(row.section_id)
        out.append(item)
    return out


@router.get("/api/sections", response_model=List[SectionResponse])
async def list_sections(
    grade_level_id: Optional[int] = None,
    _: Staff = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Section).order_by(Section.grade_level_id, Section.name)
    if grade_level_id:
        stmt = stmt.where(Section.grade_level_id == grade_level_id)
    rows = (await db.execute(stmt)).scalars().all()
    return await _decorate_sections(db, rows)


@router.post("/api/sections", response_model=SectionResponse, status_code=201)
async def create_section(
    payload: SectionCreate,
    _: Staff = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    if await db.get(GradeLevel, payload.grade_level_id) is None:
        raise HTTPException(status_code=400, detail="That grade level does not exist.")
    exists = (
        await db.execute(
            select(Section).where(Section.section_code == payload.section_code)
        )
    ).scalar_one_or_none()
    if exists:
        raise HTTPException(status_code=409, detail="That section code is already taken.")
    row = Section(**payload.model_dump())
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return (await _decorate_sections(db, [row]))[0]


@router.patch("/api/sections/{section_id}", response_model=SectionResponse)
async def update_section(
    section_id: int,
    payload: SectionUpdate,
    _: Staff = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    row = await db.get(Section, section_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Section not found.")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(row, field, value)
    await db.commit()
    await db.refresh(row)
    return (await _decorate_sections(db, [row]))[0]


@router.delete("/api/sections/{section_id}", status_code=204)
async def delete_section(
    section_id: int,
    _: Staff = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    row = await db.get(Section, section_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Section not found.")
    enrolled = (
        await db.execute(
            select(func.count())
            .select_from(StudentEnrollment)
            .where(StudentEnrollment.section_id == section_id)
        )
    ).scalar_one()
    if enrolled:
        raise HTTPException(
            status_code=409, detail=f"{enrolled} student(s) are enrolled in this section."
        )
    await db.delete(row)
    await db.commit()


# ══════════════════════════════════════════════════════════════════════════════
# Subjects
# ══════════════════════════════════════════════════════════════════════════════
@router.get("/api/subjects", response_model=List[SubjectResponse])
async def list_subjects(
    grade_level_id: Optional[int] = None,
    is_active: Optional[bool] = None,
    _: Staff = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Subject).order_by(Subject.grade_level_id, Subject.subject_name)
    if grade_level_id:
        stmt = stmt.where(Subject.grade_level_id == grade_level_id)
    if is_active is not None:
        stmt = stmt.where(Subject.is_active.is_(is_active))
    rows = (await db.execute(stmt)).scalars().all()

    levels = {
        lvl.grade_level_id: lvl.name
        for lvl in (await db.execute(select(GradeLevel))).scalars().all()
    }
    out = []
    for row in rows:
        item = SubjectResponse.model_validate(row)
        item.grade_level_name = levels.get(row.grade_level_id)
        out.append(item)
    return out


@router.post("/api/subjects", response_model=SubjectResponse, status_code=201)
async def create_subject(
    payload: SubjectCreate,
    _: Staff = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    exists = (
        await db.execute(
            select(Subject).where(Subject.subject_code == payload.subject_code)
        )
    ).scalar_one_or_none()
    if exists:
        raise HTTPException(status_code=409, detail="That subject code is already taken.")
    row = Subject(**payload.model_dump())
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return SubjectResponse.model_validate(row)


@router.patch("/api/subjects/{subject_id}", response_model=SubjectResponse)
async def update_subject(
    subject_id: int,
    payload: SubjectUpdate,
    _: Staff = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    row = await db.get(Subject, subject_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Subject not found.")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(row, field, value)
    await db.commit()
    await db.refresh(row)
    return SubjectResponse.model_validate(row)


@router.delete("/api/subjects/{subject_id}", status_code=204)
async def delete_subject(
    subject_id: int,
    _: Staff = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    row = await db.get(Subject, subject_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Subject not found.")
    await db.delete(row)
    await db.commit()


# ══════════════════════════════════════════════════════════════════════════════
# Teacher assignments
# ══════════════════════════════════════════════════════════════════════════════
@router.get("/api/teacher-assignments", response_model=List[TeacherAssignmentResponse])
async def list_assignments(
    teacher_id: Optional[uuid.UUID] = None,
    section_id: Optional[int] = None,
    _: Staff = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(TeacherAssignment)
    if teacher_id:
        stmt = stmt.where(TeacherAssignment.teacher_id == teacher_id)
    if section_id:
        stmt = stmt.where(TeacherAssignment.section_id == section_id)
    rows = (await db.execute(stmt)).scalars().all()

    teachers = {s.id: s.full_name for s in (await db.execute(select(Staff))).scalars()}
    sections = {s.section_id: s.name for s in (await db.execute(select(Section))).scalars()}
    subjects = {
        s.subject_id: s.subject_name for s in (await db.execute(select(Subject))).scalars()
    }

    out = []
    for row in rows:
        item = TeacherAssignmentResponse.model_validate(row)
        item.teacher_name = teachers.get(row.teacher_id)
        item.section_name = sections.get(row.section_id)
        item.subject_name = subjects.get(row.subject_id)
        out.append(item)
    return out


@router.post(
    "/api/teacher-assignments", response_model=TeacherAssignmentResponse, status_code=201
)
async def create_assignment(
    payload: TeacherAssignmentCreate,
    _: Staff = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    duplicate = (
        await db.execute(
            select(TeacherAssignment).where(
                TeacherAssignment.teacher_id == payload.teacher_id,
                TeacherAssignment.section_id == payload.section_id,
                TeacherAssignment.subject_id == payload.subject_id,
            )
        )
    ).scalar_one_or_none()
    if duplicate:
        raise HTTPException(status_code=409, detail="That assignment already exists.")

    if payload.is_adviser:
        current = (
            await db.execute(
                select(TeacherAssignment).where(
                    TeacherAssignment.section_id == payload.section_id,
                    TeacherAssignment.is_adviser.is_(True),
                )
            )
        ).scalars().all()
        for other in current:
            other.is_adviser = False

    row = TeacherAssignment(**payload.model_dump())
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return TeacherAssignmentResponse.model_validate(row)


@router.delete("/api/teacher-assignments/{assignment_id}", status_code=204)
async def delete_assignment(
    assignment_id: int,
    _: Staff = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    row = await db.get(TeacherAssignment, assignment_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Assignment not found.")
    await db.delete(row)
    await db.commit()


# ══════════════════════════════════════════════════════════════════════════════
# Class schedules
# ══════════════════════════════════════════════════════════════════════════════
async def _decorate_schedules(db: AsyncSession, rows) -> List[ClassScheduleResponse]:
    teachers = {s.id: s.full_name for s in (await db.execute(select(Staff))).scalars()}
    sections = {s.section_id: s.name for s in (await db.execute(select(Section))).scalars()}
    subjects = {
        s.subject_id: s.subject_name for s in (await db.execute(select(Subject))).scalars()
    }
    out = []
    for row in rows:
        item = ClassScheduleResponse.model_validate(row)
        item.teacher_name = teachers.get(row.teacher_id)
        item.section_name = sections.get(row.section_id)
        item.subject_name = subjects.get(row.subject_id)
        out.append(item)
    return out


async def _check_conflicts(
    db: AsyncSession, payload, exclude_id: int | None = None
) -> None:
    """A room, a teacher and a section can each only be in one place at a time."""
    same_day = (
        await db.execute(
            select(ClassSchedule).where(
                ClassSchedule.day_of_week == payload.day_of_week,
                ClassSchedule.school_year_id == payload.school_year_id,
            )
        )
    ).scalars().all()

    for other in same_day:
        if exclude_id is not None and other.schedule_id == exclude_id:
            continue
        if not _overlaps(
            payload.start_time, payload.end_time, other.start_time, other.end_time
        ):
            continue
        if other.teacher_id == payload.teacher_id:
            raise HTTPException(
                status_code=409,
                detail=f"That teacher already has a class on {payload.day_of_week} "
                f"at {other.start_time}–{other.end_time}.",
            )
        if other.section_id == payload.section_id:
            raise HTTPException(
                status_code=409,
                detail=f"That section already has a class on {payload.day_of_week} "
                f"at {other.start_time}–{other.end_time}.",
            )
        if other.room and payload.room and other.room == payload.room:
            raise HTTPException(
                status_code=409,
                detail=f"Room {payload.room} is taken on {payload.day_of_week} "
                f"at {other.start_time}–{other.end_time}.",
            )


@router.get("/api/class-schedules", response_model=List[ClassScheduleResponse])
async def list_schedules(
    section_id: Optional[int] = None,
    teacher_id: Optional[uuid.UUID] = None,
    school_year_id: Optional[int] = None,
    _: Staff = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(ClassSchedule).order_by(ClassSchedule.day_of_week, ClassSchedule.start_time)
    if section_id:
        stmt = stmt.where(ClassSchedule.section_id == section_id)
    if teacher_id:
        stmt = stmt.where(ClassSchedule.teacher_id == teacher_id)
    if school_year_id:
        stmt = stmt.where(ClassSchedule.school_year_id == school_year_id)
    rows = (await db.execute(stmt)).scalars().all()
    return await _decorate_schedules(db, rows)


@router.post("/api/class-schedules", response_model=ClassScheduleResponse, status_code=201)
async def create_schedule(
    payload: ClassScheduleCreate,
    _: Staff = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    if _to_minutes(payload.end_time) <= _to_minutes(payload.start_time):
        raise HTTPException(status_code=400, detail="The end time must be after the start time.")
    await _check_conflicts(db, payload)
    row = ClassSchedule(**payload.model_dump())
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return (await _decorate_schedules(db, [row]))[0]


@router.patch("/api/class-schedules/{schedule_id}", response_model=ClassScheduleResponse)
async def update_schedule(
    schedule_id: int,
    payload: ClassScheduleUpdate,
    _: Staff = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    row = await db.get(ClassSchedule, schedule_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Schedule not found.")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(row, field, value)
    if _to_minutes(row.end_time) <= _to_minutes(row.start_time):
        raise HTTPException(status_code=400, detail="The end time must be after the start time.")
    await _check_conflicts(db, row, exclude_id=schedule_id)
    await db.commit()
    await db.refresh(row)
    return (await _decorate_schedules(db, [row]))[0]


@router.delete("/api/class-schedules/{schedule_id}", status_code=204)
async def delete_schedule(
    schedule_id: int,
    _: Staff = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    row = await db.get(ClassSchedule, schedule_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Schedule not found.")
    await db.delete(row)
    await db.commit()


# ══════════════════════════════════════════════════════════════════════════════
# Teacher availability
# ══════════════════════════════════════════════════════════════════════════════
@router.get("/api/teacher-availabilities", response_model=List[AvailabilityResponse])
async def list_availabilities(
    teacher_id: Optional[uuid.UUID] = None,
    _: Staff = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(TeacherAvailability).where(TeacherAvailability.is_active.is_(True))
    if teacher_id:
        stmt = stmt.where(TeacherAvailability.teacher_id == teacher_id)
    rows = (await db.execute(stmt)).scalars().all()
    teachers = {s.id: s.full_name for s in (await db.execute(select(Staff))).scalars()}
    out = []
    for row in rows:
        item = AvailabilityResponse.model_validate(row)
        item.teacher_name = teachers.get(row.teacher_id)
        out.append(item)
    return out


@router.post(
    "/api/teacher-availabilities", response_model=AvailabilityResponse, status_code=201
)
async def create_availability(
    payload: AvailabilityCreate,
    current: Staff = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if current.role != "admin" and payload.teacher_id != current.id:
        raise HTTPException(
            status_code=403, detail="You can only publish your own availability."
        )
    row = TeacherAvailability(**payload.model_dump())
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return AvailabilityResponse.model_validate(row)


@router.delete("/api/teacher-availabilities/{availability_id}", status_code=204)
async def delete_availability(
    availability_id: int,
    current: Staff = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    row = await db.get(TeacherAvailability, availability_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Availability not found.")
    if current.role != "admin" and row.teacher_id != current.id:
        raise HTTPException(status_code=403, detail="That entry is not yours.")
    await db.delete(row)
    await db.commit()
