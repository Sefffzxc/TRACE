"""Teacher-only creation of a section's shared student roster.

Roster creation is separate from grading: advisers may add learners without
receiving permission to edit the subjects those learners take.
"""
from __future__ import annotations

from typing import Optional

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.scoping import get_scoped_optional
from app.models.academic import GradeLevel, Section
from app.models.schools import School
from app.models.staff import ROLE_TEACHER, Staff
from app.services.access import class_access
from app.services.school_years import require_current_year_record, resolve_school_year_id


async def require_roster_section(
    db: AsyncSession,
    current: Staff,
    scope: int,
    section_id: Optional[int],
    grade_level_id: Optional[int] = None,
    school_year_id: Optional[int] = None,
) -> Section:
    """Validate the creation destination before any learner is written.

    Principals' general visibility does not grant student creation. A teacher
    must teach a subject or independently advise the destination section.
    This read-only check is also safe for import previews.
    """
    if current.role != ROLE_TEACHER or current.school_id != scope:
        raise HTTPException(
            status_code=403,
            detail="Only teachers may add students to sections they teach or advise.",
        )
    if section_id is None:
        raise HTTPException(status_code=400, detail="Choose a section before adding students.")
    section = await get_scoped_optional(db, Section, section_id, scope)
    if section is None:
        raise HTTPException(status_code=400, detail="That section does not exist.")
    year_id = await resolve_school_year_id(db, scope, required=True)
    if school_year_id is not None:
        await require_current_year_record(db, scope, school_year_id)
    access = await class_access(db, current, scope, year_id)
    if section.section_id not in access.sections:
        raise HTTPException(
            status_code=403,
            detail="You can only add students to a section you teach or advise.",
        )
    grade_id = section.grade_level_id if grade_level_id is None else grade_level_id
    if await get_scoped_optional(db, GradeLevel, grade_id, scope) is None:
        raise HTTPException(status_code=400, detail="That grade level does not exist.")
    if grade_id != section.grade_level_id:
        raise HTTPException(status_code=400, detail="That section does not belong to the selected grade level.")
    return section


async def lock_roster_section(db: AsyncSession, scope: int, section_id: int) -> Section:
    """Serialize roster matching/writes with other manual and import requests.

    Import matching must run after acquiring this lock and before committing.
    Preview requests must use require_roster_section only, since SQLite needs
    a no-op UPDATE to obtain its writer lock.
    """
    if db.get_bind().dialect.name == "sqlite":
        await db.execute(update(Section).where(
            Section.section_id == section_id, Section.school_id == scope,
        ).values(section_id=Section.section_id).execution_options(synchronize_session=False))
    else:
        await db.execute(select(School.school_id).where(School.school_id == scope)
                         .with_for_update(read=True, key_share=True))
    section = await db.scalar(select(Section).where(
        Section.section_id == section_id, Section.school_id == scope,
    ).with_for_update().execution_options(populate_existing=True))
    if section is None:
        raise HTTPException(status_code=400, detail="That section does not exist.")
    return section
