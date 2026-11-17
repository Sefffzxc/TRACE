"""Resolve an academic year without mixing archived and current records."""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.academic import SchoolYear
from app.models.schools import School


async def resolve_school_year_id(
    db: AsyncSession,
    school_id: int,
    given: int | None = None,
    *,
    required: bool = False,
) -> int | None:
    """Use an explicit school-owned year, or only the school's current year.

    A missing current year is an empty academic view, never permission to
    load every year. Writes pass ``required=True`` to ask for a clear setup
    error instead. A shared lock holds the selected year during a write so a
    concurrent year switch cannot silently retarget that operation.
    """
    if required and db.get_bind().dialect.name != "sqlite":
        # School-year switches take the exclusive version of this lock.
        # Acquire it before the year row to keep assignment/roster lock order
        # consistent and prevent a switch during a save.
        await db.execute(select(School.school_id).where(School.school_id == school_id)
                         .with_for_update(read=True, key_share=True))
    stmt = select(SchoolYear).where(SchoolYear.school_id == school_id)
    if given is not None:
        stmt = stmt.where(SchoolYear.school_year_id == given)
    else:
        stmt = stmt.where(SchoolYear.is_current.is_(True))
    if required:
        stmt = stmt.with_for_update(read=True)
    rows = (await db.scalars(stmt.execution_options(populate_existing=True))).all()
    if len(rows) > 1:
        raise HTTPException(status_code=409, detail="More than one current school year is configured. Ask your administrator to select one.")
    if not rows:
        if given is not None:
            raise HTTPException(status_code=400, detail="That school year does not belong to this school.")
        if required:
            raise HTTPException(status_code=400, detail="Select a current school year before adding academic records.")
        return None
    return rows[0].school_year_id


async def current_year_id(db: AsyncSession, school_id: int) -> int | None:
    return await resolve_school_year_id(db, school_id)


async def require_current_year_record(
    db: AsyncSession, school_id: int, school_year_id: int | None,
) -> int:
    """Reject a stale browser's attempt to change a different year's row."""
    selected = await resolve_school_year_id(db, school_id, required=True)
    if school_year_id is None or school_year_id != selected:
        raise HTTPException(status_code=409, detail="The current school year changed. Refresh this page before saving.")
    return selected
