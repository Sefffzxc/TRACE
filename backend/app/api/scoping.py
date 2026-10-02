"""Helpers that keep one school's queries inside that school.

Every read and write of a school's records goes through one of these. They are
small on purpose: the point is that a reviewer can read this file in a minute
and then check that every call site uses it, rather than having to verify fifty
hand-written WHERE clauses.

The pattern throughout the API is:

    scope: int = Depends(school_scope)          # refuses the super admin
    rows = (await db.execute(scoped(select(Student), Student, scope))).scalars()

and for a single record addressed by id:

    student = await get_scoped(db, Student, student_id, scope, "Student")

`get_scoped` returns 404 — not 403 — when a record belongs to another school.
That is deliberate. Answering "forbidden" would confirm the record exists, so a
teacher at one school could learn another school's student ids by probing. As
far as anyone outside a school can tell, its records simply are not there.
"""
from __future__ import annotations

from typing import Any, Optional, Type

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession


def scoped(stmt, model: Type[Any], school_id: int):
    """Restrict a select to one school."""
    return stmt.where(model.school_id == school_id)


async def get_scoped(
    db: AsyncSession,
    model: Type[Any],
    pk: Any,
    school_id: int,
    label: str = "Record",
) -> Any:
    """Fetch one row by primary key, but only if it belongs to this school.

    Raises 404 both when the row does not exist and when it belongs to someone
    else, so the two are indistinguishable from outside.
    """
    row = await db.get(model, pk)
    if row is None or getattr(row, "school_id", None) != school_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"{label} not found."
        )
    return row


async def get_scoped_optional(
    db: AsyncSession, model: Type[Any], pk: Any, school_id: int
) -> Optional[Any]:
    """Same, but returns None instead of raising."""
    if pk is None:
        return None
    row = await db.get(model, pk)
    if row is None or getattr(row, "school_id", None) != school_id:
        return None
    return row
