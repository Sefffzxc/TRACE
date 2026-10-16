"""Class management: school years, grade levels, sections, subjects and
teacher assignments."""
from __future__ import annotations

import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_admin, get_current_user, school_scope
from app.api.scoping import get_scoped, get_scoped_optional, scoped
from app.db.session import get_db
from app.models.academic import (
    GradeLevel,
    SchoolYear,
    Section,
    Subject,
    TeacherAssignment,
)
from app.models.staff import Staff
from app.models.students import StudentEnrollment
from app.services.access import class_access
from app.schemas.academic import (
    GradeLevelCreate,
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
                select(GradeLevel).order_by(
                    GradeLevel.sort_order, GradeLevel.grade_level_id
                ),
                GradeLevel,
                scope,
            )
        )
    ).scalars().all()

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
async def create_grade_level(
    payload: GradeLevelCreate,
    _: Staff = Depends(get_current_admin),
    scope: int = Depends(school_scope),
    db: AsyncSession = Depends(get_db),
):
    exists = (
        await db.execute(
            scoped(
                select(GradeLevel).where(GradeLevel.level == payload.level),
                GradeLevel,
                scope,
            )
        )
    ).scalar_one_or_none()
    if exists:
        raise HTTPException(status_code=409, detail="That grade level already exists.")
    row = GradeLevel(**payload.model_dump(), school_id=scope)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return GradeLevelResponse.model_validate(row)


@router.patch("/api/grade-levels/{grade_level_id}", response_model=GradeLevelResponse)
async def update_grade_level(
    grade_level_id: int,
    payload: GradeLevelUpdate,
    _: Staff = Depends(get_current_admin),
    scope: int = Depends(school_scope),
    db: AsyncSession = Depends(get_db),
):
    row = await get_scoped(db, GradeLevel, grade_level_id, scope, "Grade level")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(row, field, value)
    await db.commit()
    await db.refresh(row)
    return GradeLevelResponse.model_validate(row)


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
        select(Section).order_by(Section.grade_level_id, Section.name), Section, scope
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
    exists = (
        await db.execute(
            scoped(
                select(Section).where(Section.section_code == payload.section_code),
                Section,
                scope,
            )
        )
    ).scalar_one_or_none()
    if exists:
        raise HTTPException(status_code=409, detail="That section code is already taken.")
    row = Section(**payload.model_dump(), school_id=scope)
    db.add(row)
    await db.commit()
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
    data = payload.model_dump(exclude_unset=True)
    if data.get("grade_level_id") is not None and (
        await get_scoped_optional(db, GradeLevel, data["grade_level_id"], scope) is None
    ):
        raise HTTPException(status_code=400, detail="That grade level does not exist.")
    for field, value in data.items():
        setattr(row, field, value)
    await db.commit()
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
    enrolled = (
        await db.execute(
            scoped(
                select(func.count())
                .select_from(StudentEnrollment)
                .where(StudentEnrollment.section_id == section_id),
                StudentEnrollment,
                scope,
            )
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
    scope: int = Depends(school_scope),
    db: AsyncSession = Depends(get_db),
):
    stmt = scoped(
        select(Subject).order_by(Subject.grade_level_id, Subject.subject_name),
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
    exists = (
        await db.execute(
            scoped(
                select(Subject).where(Subject.subject_code == payload.subject_code),
                Subject,
                scope,
            )
        )
    ).scalar_one_or_none()
    if exists:
        raise HTTPException(status_code=409, detail="That subject code is already taken.")
    row = Subject(**payload.model_dump(), school_id=scope)
    db.add(row)
    await db.commit()
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
    data = payload.model_dump(exclude_unset=True)
    if data.get("grade_level_id") is not None and (
        await get_scoped_optional(db, GradeLevel, data["grade_level_id"], scope) is None
    ):
        raise HTTPException(status_code=400, detail="That grade level does not exist.")
    for field, value in data.items():
        setattr(row, field, value)
    await db.commit()
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
    if await get_scoped_optional(db, Staff, payload.teacher_id, scope) is None:
        raise HTTPException(status_code=400, detail="That teacher does not exist.")
    if await get_scoped_optional(db, Section, payload.section_id, scope) is None:
        raise HTTPException(status_code=400, detail="That section does not exist.")
    if payload.subject_id is not None and (
        await get_scoped_optional(db, Subject, payload.subject_id, scope) is None
    ):
        raise HTTPException(status_code=400, detail="That subject does not exist.")

    duplicate = (
        await db.execute(
            scoped(
                select(TeacherAssignment).where(
                    TeacherAssignment.teacher_id == payload.teacher_id,
                    TeacherAssignment.section_id == payload.section_id,
                    TeacherAssignment.subject_id == payload.subject_id,
                ),
                TeacherAssignment,
                scope,
            )
        )
    ).scalar_one_or_none()
    if duplicate:
        raise HTTPException(status_code=409, detail="That assignment already exists.")

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
    await db.commit()
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
