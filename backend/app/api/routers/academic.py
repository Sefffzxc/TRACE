"""Class management: school years, grade levels, sections, subjects and
teacher assignments."""
from __future__ import annotations

import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_admin, get_current_user, school_scope
from app.api.scoping import get_scoped, get_scoped_optional, scoped
from app.db.session import get_db
from app.models.academic import (
    ClassSchedule,
    GradeLevel,
    SchoolYear,
    Section,
    Subject,
    TeacherAssignment,
)
from app.models.schools import School
from app.models.staff import ROLE_TEACHER, STATUS_APPROVED, Staff
from app.models.students import ClassRecordSetup, StudentEnrollment
from app.services.access import class_access
from app.schemas.academic import (
    GradeLevelCreate,
    GradeSectionInput,
    grade_level_rank,
    school_year_dates,
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


async def _name_exists(db, model, name_column, name, grade_level_id, scope, exclude_id=None):
    stmt = select(model).where(
        model.school_id == scope,
        model.grade_level_id == grade_level_id,
        func.lower(func.trim(name_column)) == name.strip().lower(),
    )
    if exclude_id is not None:
        key = Section.section_id if model is Section else Subject.subject_id
        stmt = stmt.where(key != exclude_id)
    return (await db.execute(stmt)).scalars().first() is not None


async def _assert_section_unused(db: AsyncSession, section: Section, scope: int) -> None:
    for model, label in (
        (StudentEnrollment, "enrollment records"),
        (TeacherAssignment, "teacher assignments"),
        (ClassRecordSetup, "class records"),
        (ClassSchedule, "saved schedules"),
    ):
        count = (await db.execute(select(func.count()).select_from(model).where(
            model.school_id == scope, model.section_id == section.section_id,
        ))).scalar_one()
        if count:
            raise HTTPException(
                status_code=409,
                detail=f'Cannot remove or move section "{section.name}": it has {label}.',
            )


async def _sync_grade_sections(
    db: AsyncSession, grade: GradeLevel, sections: list[GradeSectionInput], scope: int,
) -> None:
    """Apply a complete section list without replacing existing identities."""
    existing = {row.section_id: row for row in (await db.execute(
        select(Section).where(Section.school_id == scope, Section.grade_level_id == grade.grade_level_id)
    )).scalars().all()}
    seen_ids = set()
    seen_names = set()
    for item in sections:
        name_key = item.name.casefold()
        if name_key in seen_names:
            raise HTTPException(status_code=409, detail="Each section in a grade must have a different name.")
        seen_names.add(name_key)
        if item.section_id is not None:
            if item.section_id not in existing:
                raise HTTPException(status_code=400, detail="A section does not belong to this grade level.")
            if item.section_id in seen_ids:
                raise HTTPException(status_code=400, detail="A section was included more than once.")
            seen_ids.add(item.section_id)

    removed = [row for key, row in existing.items() if key not in seen_ids]
    for row in removed:
        await _assert_section_unused(db, row, scope)
    for row in removed:
        await db.delete(row)
    await db.flush()

    # Temporary names let two existing sections exchange names in one save.
    for item in sections:
        if item.section_id is not None and existing[item.section_id].name != item.name:
            existing[item.section_id].name = f"~{uuid.uuid4().hex}"
    await db.flush()
    for item in sections:
        if item.section_id is None:
            db.add(Section(
                school_id=scope, grade_level_id=grade.grade_level_id,
                name=item.name, capacity=item.capacity,
            ))
        else:
            row = existing[item.section_id]
            row.name, row.capacity = item.name, item.capacity
    await db.flush()


async def _commit_structure(db: AsyncSession) -> None:
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="That name already exists in this grade level.") from None


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
    _: Staff = Depends(get_current_user),
    scope: int = Depends(school_scope),
    db: AsyncSession = Depends(get_db),
):
    rows = (
        await db.execute(
            scoped(
                select(SchoolYear).order_by(SchoolYear.start_date.desc()),
                SchoolYear,
                scope,
            )
        )
    ).scalars().all()
    return [SchoolYearResponse.model_validate(r) for r in rows]


@router.get("/api/school-years/current", response_model=Optional[SchoolYearResponse])
async def current_school_year(
    _: Staff = Depends(get_current_user),
    scope: int = Depends(school_scope),
    db: AsyncSession = Depends(get_db),
):
    row = (
        await db.execute(
            scoped(
                select(SchoolYear).where(SchoolYear.is_current.is_(True)),
                SchoolYear,
                scope,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = (
            await db.execute(
                scoped(
                    select(SchoolYear).order_by(SchoolYear.start_date.desc()).limit(1),
                    SchoolYear,
                    scope,
                )
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
    scope: int = Depends(school_scope),
    db: AsyncSession = Depends(get_db),
):
    exists = (
        await db.execute(
            scoped(
                select(SchoolYear).where(SchoolYear.year == payload.year),
                SchoolYear,
                scope,
            )
        )
    ).scalar_one_or_none()
    if exists:
        raise HTTPException(status_code=409, detail="That school year already exists.")

    start, end = school_year_dates(payload.year)
    row = SchoolYear(**payload.model_dump(), start_date=start, end_date=end, school_id=scope)
    if row.is_current:
        for other in (
            await db.execute(scoped(select(SchoolYear), SchoolYear, scope))
        ).scalars().all():
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
    scope: int = Depends(school_scope),
    db: AsyncSession = Depends(get_db),
):
    row = await get_scoped(db, SchoolYear, school_year_id, scope, "School year")
    data = payload.model_dump(exclude_unset=True)
    if data.get("year"):
        data["start_date"], data["end_date"] = school_year_dates(data["year"])
    if data.get("is_current"):
        for other in (
            await db.execute(scoped(select(SchoolYear), SchoolYear, scope))
        ).scalars().all():
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
    scope: int = Depends(school_scope),
    db: AsyncSession = Depends(get_db),
):
    row = await get_scoped(db, SchoolYear, school_year_id, scope, "School year")
    in_use = (
        await db.execute(
            scoped(
                select(func.count())
                .select_from(StudentEnrollment)
                .where(StudentEnrollment.school_year_id == school_year_id),
                StudentEnrollment,
                scope,
            )
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
    _: Staff = Depends(get_current_user),
    scope: int = Depends(school_scope),
    db: AsyncSession = Depends(get_db),
):
    rows = (
        await db.execute(
            scoped(
                select(GradeLevel).order_by(GradeLevel.grade_level_id),
                GradeLevel,
                scope,
            )
        )
    ).scalars().all()
    # By the number in the level, so Grade 10 comes after Grade 7.
    rows = sorted(rows, key=lambda row: grade_level_rank(row.level))

    section_counts = dict(
        (
            await db.execute(
                scoped(
                    select(Section.grade_level_id, func.count()).group_by(
                        Section.grade_level_id
                    ),
                    Section,
                    scope,
                )
            )
        ).all()
    )
    student_counts = dict(
        (
            await db.execute(
                scoped(
                    select(StudentEnrollment.grade_level_id, func.count())
                    .where(StudentEnrollment.is_active.is_(True))
                    .group_by(StudentEnrollment.grade_level_id),
                    StudentEnrollment,
                    scope,
                )
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
@router.post("/api/grade-levels/with-sections", response_model=GradeLevelResponse, status_code=201)
async def create_grade_level(
    payload: GradeLevelCreate,
    _: Staff = Depends(get_current_admin),
    scope: int = Depends(school_scope),
    db: AsyncSession = Depends(get_db),
):
    exists = (
        await db.execute(
            scoped(
                select(GradeLevel).where(func.lower(GradeLevel.level) == payload.level.lower()),
                GradeLevel,
                scope,
            )
        )
    ).scalar_one_or_none()
    if exists:
        raise HTTPException(status_code=409, detail="That grade level already exists.")
    row = GradeLevel(
        **payload.model_dump(exclude={"sections"}), sort_order=grade_level_rank(payload.level), school_id=scope
    )
    db.add(row)
    try:
        await db.flush()
        await _sync_grade_sections(db, row, payload.sections, scope)
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="That grade or section name already exists.") from None
    await db.refresh(row)
    item = GradeLevelResponse.model_validate(row)
    item.section_count = len(payload.sections)
    return item


@router.patch("/api/grade-levels/{grade_level_id}", response_model=GradeLevelResponse)
@router.patch("/api/grade-levels/{grade_level_id}/with-sections", response_model=GradeLevelResponse)
async def update_grade_level(
    grade_level_id: int,
    payload: GradeLevelUpdate,
    _: Staff = Depends(get_current_admin),
    scope: int = Depends(school_scope),
    db: AsyncSession = Depends(get_db),
):
    row = await get_scoped(db, GradeLevel, grade_level_id, scope, "Grade level")
    data = payload.model_dump(exclude_unset=True, exclude_none=True, exclude={"sections"})
    if "level" in data:
        duplicate = (await db.execute(select(GradeLevel).where(
            GradeLevel.school_id == scope, GradeLevel.grade_level_id != grade_level_id,
            func.lower(GradeLevel.level) == data["level"].lower(),
        ))).scalars().first()
        if duplicate:
            raise HTTPException(status_code=409, detail="That grade level already exists.")
    for field, value in data.items():
        setattr(row, field, value)
    row.sort_order = grade_level_rank(row.level)
    try:
        if payload.sections is not None:
            await _sync_grade_sections(db, row, payload.sections, scope)
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="That grade or section name already exists.") from None
    await db.refresh(row)
    item = GradeLevelResponse.model_validate(row)
    item.section_count = (await db.execute(select(func.count()).select_from(Section).where(
        Section.school_id == scope, Section.grade_level_id == grade_level_id,
    ))).scalar_one()
    return item


@router.delete("/api/grade-levels/{grade_level_id}", status_code=204)
async def delete_grade_level(
    grade_level_id: int,
    _: Staff = Depends(get_current_admin),
    scope: int = Depends(school_scope),
    db: AsyncSession = Depends(get_db),
):
    row = await get_scoped(db, GradeLevel, grade_level_id, scope, "Grade level")
    enrolled = (
        await db.execute(
            scoped(
                select(func.count())
                .select_from(StudentEnrollment)
                .where(StudentEnrollment.grade_level_id == grade_level_id),
                StudentEnrollment,
                scope,
            )
        )
    ).scalar_one()
    if enrolled:
        raise HTTPException(
            status_code=409,
            detail=f"{enrolled} student(s) are enrolled in this grade level.",
        )
    for model in (Section, Subject):
        count = (await db.execute(select(func.count()).select_from(model).where(
            model.school_id == scope, model.grade_level_id == grade_level_id,
        ))).scalar_one()
        if count:
            raise HTTPException(
                status_code=409, detail="Remove this grade's sections and subjects before removing the grade level.",
            )
    await db.delete(row)
    await db.commit()


# ══════════════════════════════════════════════════════════════════════════════
# Sections
# ══════════════════════════════════════════════════════════════════════════════
async def _decorate_sections(
    db: AsyncSession, rows, scope: int
) -> List[SectionResponse]:
    levels = {
        lvl.grade_level_id: lvl.name
        for lvl in (
            await db.execute(scoped(select(GradeLevel), GradeLevel, scope))
        ).scalars().all()
    }
    counts = dict(
        (
            await db.execute(
                scoped(
                    select(StudentEnrollment.section_id, func.count())
                    .where(StudentEnrollment.is_active.is_(True))
                    .group_by(StudentEnrollment.section_id),
                    StudentEnrollment,
                    scope,
                )
            )
        ).all()
    )
    advisers = {}
    for assignment, staff in (
        await db.execute(
            scoped(
                select(TeacherAssignment, Staff)
                .join(Staff, Staff.id == TeacherAssignment.teacher_id)
                .where(
                    TeacherAssignment.is_adviser.is_(True),
                    Staff.school_id == scope,
                ),
                TeacherAssignment,
                scope,
            )
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
    scope: int = Depends(school_scope),
    db: AsyncSession = Depends(get_db),
):
    stmt = scoped(
        select(Section)
        .join(GradeLevel, GradeLevel.grade_level_id == Section.grade_level_id)
        .order_by(GradeLevel.sort_order, GradeLevel.grade_level_id, Section.name),
        Section,
        scope,
    )
    if grade_level_id:
        stmt = stmt.where(Section.grade_level_id == grade_level_id)
    rows = (await db.execute(stmt)).scalars().all()
    return await _decorate_sections(db, rows, scope)


@router.post("/api/sections", response_model=SectionResponse, status_code=201)
async def create_section(
    payload: SectionCreate,
    _: Staff = Depends(get_current_admin),
    scope: int = Depends(school_scope),
    db: AsyncSession = Depends(get_db),
):
    if await get_scoped_optional(db, GradeLevel, payload.grade_level_id, scope) is None:
        raise HTTPException(status_code=400, detail="That grade level does not exist.")
    if await _name_exists(db, Section, Section.name, payload.name, payload.grade_level_id, scope):
        raise HTTPException(status_code=409, detail="That section name already exists in this grade level.")
    row = Section(**payload.model_dump(), school_id=scope)
    db.add(row)
    await _commit_structure(db)
    await db.refresh(row)
    return (await _decorate_sections(db, [row], scope))[0]


@router.patch("/api/sections/{section_id}", response_model=SectionResponse)
async def update_section(
    section_id: int,
    payload: SectionUpdate,
    _: Staff = Depends(get_current_admin),
    scope: int = Depends(school_scope),
    db: AsyncSession = Depends(get_db),
):
    row = await get_scoped(db, Section, section_id, scope, "Section")
    data = payload.model_dump(exclude_unset=True, exclude_none=True)
    if data.get("grade_level_id") is not None and (
        await get_scoped_optional(db, GradeLevel, data["grade_level_id"], scope) is None
    ):
        raise HTTPException(status_code=400, detail="That grade level does not exist.")
    if data.get("grade_level_id", row.grade_level_id) != row.grade_level_id:
        await _assert_section_unused(db, row, scope)
    if await _name_exists(
        db, Section, Section.name, data.get("name", row.name),
        data.get("grade_level_id", row.grade_level_id), scope, section_id,
    ):
        raise HTTPException(status_code=409, detail="That section name already exists in this grade level.")
    for field, value in data.items():
        setattr(row, field, value)
    await _commit_structure(db)
    await db.refresh(row)
    return (await _decorate_sections(db, [row], scope))[0]


@router.delete("/api/sections/{section_id}", status_code=204)
async def delete_section(
    section_id: int,
    _: Staff = Depends(get_current_admin),
    scope: int = Depends(school_scope),
    db: AsyncSession = Depends(get_db),
):
    row = await get_scoped(db, Section, section_id, scope, "Section")
    await _assert_section_unused(db, row, scope)
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
    scope: int = Depends(school_scope),
    db: AsyncSession = Depends(get_db),
):
    stmt = scoped(
        select(Subject)
        .join(GradeLevel, GradeLevel.grade_level_id == Subject.grade_level_id)
        .order_by(GradeLevel.sort_order, GradeLevel.grade_level_id, Subject.subject_name),
        Subject,
        scope,
    )
    if grade_level_id:
        stmt = stmt.where(Subject.grade_level_id == grade_level_id)
    if is_active is not None:
        stmt = stmt.where(Subject.is_active.is_(is_active))
    rows = (await db.execute(stmt)).scalars().all()

    levels = {
        lvl.grade_level_id: lvl.name
        for lvl in (
            await db.execute(scoped(select(GradeLevel), GradeLevel, scope))
        ).scalars().all()
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
    scope: int = Depends(school_scope),
    db: AsyncSession = Depends(get_db),
):
    if await get_scoped_optional(db, GradeLevel, payload.grade_level_id, scope) is None:
        raise HTTPException(status_code=400, detail="That grade level does not exist.")
    if await _name_exists(db, Subject, Subject.subject_name, payload.subject_name, payload.grade_level_id, scope):
        raise HTTPException(status_code=409, detail="That subject name already exists in this grade level.")
    row = Subject(**payload.model_dump(), school_id=scope)
    db.add(row)
    await _commit_structure(db)
    await db.refresh(row)
    return SubjectResponse.model_validate(row)


@router.patch("/api/subjects/{subject_id}", response_model=SubjectResponse)
async def update_subject(
    subject_id: int,
    payload: SubjectUpdate,
    _: Staff = Depends(get_current_admin),
    scope: int = Depends(school_scope),
    db: AsyncSession = Depends(get_db),
):
    row = await get_scoped(db, Subject, subject_id, scope, "Subject")
    data = payload.model_dump(exclude_unset=True, exclude_none=True)
    if data.get("grade_level_id") is not None and (
        await get_scoped_optional(db, GradeLevel, data["grade_level_id"], scope) is None
    ):
        raise HTTPException(status_code=400, detail="That grade level does not exist.")
    if await _name_exists(
        db, Subject, Subject.subject_name, data.get("subject_name", row.subject_name),
        data.get("grade_level_id", row.grade_level_id), scope, subject_id,
    ):
        raise HTTPException(status_code=409, detail="That subject name already exists in this grade level.")
    for field, value in data.items():
        setattr(row, field, value)
    await _commit_structure(db)
    await db.refresh(row)
    return SubjectResponse.model_validate(row)


@router.delete("/api/subjects/{subject_id}", status_code=204)
async def delete_subject(
    subject_id: int,
    _: Staff = Depends(get_current_admin),
    scope: int = Depends(school_scope),
    db: AsyncSession = Depends(get_db),
):
    row = await get_scoped(db, Subject, subject_id, scope, "Subject")
    await db.delete(row)
    await db.commit()


# ══════════════════════════════════════════════════════════════════════════════
# Teacher assignments
# ══════════════════════════════════════════════════════════════════════════════
async def _lock_assignment_section(db: AsyncSession, section_id: int, scope: int):
    """Serialize assignment writes without altering existing assignment rows."""
    if db.get_bind().dialect.name == "sqlite":
        # SQLite ignores FOR UPDATE. A no-op write acquires its transaction's
        # writer lock before checking occupancy, including across API workers.
        await db.execute(update(Section).where(
            Section.section_id == section_id, Section.school_id == scope,
        ).values(section_id=Section.section_id).execution_options(synchronize_session=False))
    else:
        # Follow the same school-before-staff order as application transfers.
        # Key-share permits other assignments while preventing school deletion.
        await db.execute(select(School.school_id).where(School.school_id == scope)
                         .with_for_update(read=True, key_share=True))
    return await db.scalar(select(Section).where(
        Section.section_id == section_id, Section.school_id == scope,
    ).with_for_update().execution_options(populate_existing=True))


@router.get("/api/teacher-assignments", response_model=List[TeacherAssignmentResponse])
async def list_assignments(
    teacher_id: Optional[uuid.UUID] = None,
    section_id: Optional[int] = None,
    _: Staff = Depends(get_current_user),
    scope: int = Depends(school_scope),
    db: AsyncSession = Depends(get_db),
):
    stmt = scoped(select(TeacherAssignment), TeacherAssignment, scope)
    if teacher_id:
        stmt = stmt.where(TeacherAssignment.teacher_id == teacher_id)
    if section_id:
        stmt = stmt.where(TeacherAssignment.section_id == section_id)
    rows = (await db.execute(stmt)).scalars().all()

    teachers = {
        s.id: s.full_name
        for s in (await db.execute(scoped(select(Staff), Staff, scope))).scalars()
    }
    sections = {
        s.section_id: s.name
        for s in (await db.execute(scoped(select(Section), Section, scope))).scalars()
    }
    subjects = {
        s.subject_id: s.subject_name
        for s in (await db.execute(scoped(select(Subject), Subject, scope))).scalars()
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
    scope: int = Depends(school_scope),
    db: AsyncSession = Depends(get_db),
):
    # The teacher, section and subject all have to be this school's own. A
    # Staff row with a NULL school_id — the super admin — never matches.
    section = await _lock_assignment_section(db, payload.section_id, scope)
    if section is None:
        raise HTTPException(status_code=400, detail="That section does not exist.")
    teacher = await db.scalar(select(Staff).where(
        Staff.id == payload.teacher_id, Staff.school_id == scope,
    ).with_for_update().execution_options(populate_existing=True))
    if teacher is None:
        raise HTTPException(status_code=400, detail="That teacher does not exist.")
    if teacher.role != ROLE_TEACHER or not teacher.is_active or not teacher.is_approved or teacher.account_status != STATUS_APPROVED:
        raise HTTPException(status_code=400, detail="Choose an active, approved teacher.")
    subject = None
    if payload.subject_id is not None:
        subject = await get_scoped_optional(db, Subject, payload.subject_id, scope)
        if subject is None:
            raise HTTPException(status_code=400, detail="That subject does not exist.")
        if subject.grade_level_id != section.grade_level_id:
            raise HTTPException(status_code=400, detail="That subject does not belong to the section's grade level.")

    occupied = select(TeacherAssignment).where(
        TeacherAssignment.school_id == scope,
        TeacherAssignment.section_id == payload.section_id,
    )
    if payload.subject_id is not None:
        occupied = occupied.where(or_(
            TeacherAssignment.subject_id == payload.subject_id,
            TeacherAssignment.subject_id.is_(None),
        ))
    existing = (await db.scalars(occupied.order_by(TeacherAssignment.assignment_id).limit(1))).first()
    if existing is not None:
        assignee = await get_scoped_optional(db, Staff, existing.teacher_id, scope)
        assignee_name = assignee.full_name if assignee is not None else "another teacher"
        existing_subject = await get_scoped_optional(db, Subject, existing.subject_id, scope)
        subject_name = existing_subject.subject_name if existing_subject is not None else (
            "all subjects" if existing.subject_id is None else "an existing subject"
        )
        raise HTTPException(status_code=409, detail=(
            f'{section.name} / {subject_name} is already assigned to {assignee_name}. '
            "Remove the existing assignment before assigning another teacher."
        ))

    if payload.is_adviser:
        current = (
            await db.execute(
                scoped(
                    select(TeacherAssignment).where(
                        TeacherAssignment.section_id == payload.section_id,
                        TeacherAssignment.is_adviser.is_(True),
                    ),
                    TeacherAssignment,
                    scope,
                )
            )
        ).scalars().all()
        for other in current:
            other.is_adviser = False

    row = TeacherAssignment(**payload.model_dump(), school_id=scope)
    db.add(row)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="The assignment changed. Refresh assignments and try again.") from None
    await db.refresh(row)
    return TeacherAssignmentResponse.model_validate(row)


@router.delete("/api/teacher-assignments/{assignment_id}", status_code=204)
async def delete_assignment(
    assignment_id: int,
    _: Staff = Depends(get_current_admin),
    scope: int = Depends(school_scope),
    db: AsyncSession = Depends(get_db),
):
    row = await get_scoped(db, TeacherAssignment, assignment_id, scope, "Assignment")
    await _lock_assignment_section(db, row.section_id, scope)
    row = await db.scalar(select(TeacherAssignment).where(
        TeacherAssignment.assignment_id == assignment_id, TeacherAssignment.school_id == scope,
    ).execution_options(populate_existing=True))
    if row is None:
        raise HTTPException(status_code=404, detail="Assignment not found.")
    await db.delete(row)
    await db.commit()


# ══════════════════════════════════════════════════════════════════════════════
# The signed-in teacher's own classes
# ══════════════════════════════════════════════════════════════════════════════
@router.get("/api/my-classes")
async def my_classes(
    current: Staff = Depends(get_current_user),
    scope: int = Depends(school_scope),
    db: AsyncSession = Depends(get_db),
):
    """The sections and subjects this person grades, for the class record's
    pickers. ``grade`` maps a section id to subject names, or to ["*"] for
    every subject; ``unrestricted`` is true for an administrator. The server
    enforces the same rule on every save (app/services/access.py)."""
    access = await class_access(db, current, scope)
    return {
        "unrestricted": access.unrestricted,
        "grade": {str(k): sorted(v) for k, v in access.grade.items()},
        "advisory": sorted(access.advisory),
    }
