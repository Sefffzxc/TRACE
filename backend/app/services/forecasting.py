"""Choose completed forecast checkpoints consistently for saves and re-runs."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.scoping import scoped
from app.models.students import StudentPace
from app.services import ml_client


async def forecast_records(
    db: AsyncSession, rows: list[StudentPace], school_id: int
) -> list[tuple[StudentPace, StudentPace | None]]:
    """One pair per enrollment and subject; unfinished second periods stay out.

    In-memory rows win over stored values, including a record just cleared by
    the teacher. This also works during imports with autoflush disabled.
    Enrollment IDs keep different school years from being paired together.
    """
    wanted = {(r.enrollment_id, r.subject) for r in rows
              if r.enrollment_id and r.school_id == school_id}
    if not wanted:
        return []
    stored = (await db.execute(scoped(select(StudentPace).where(
        StudentPace.enrollment_id.in_({e for e, _ in wanted}),
        StudentPace.grading_period.in_((1, 2)),
    ).order_by(StudentPace.pace_id), StudentPace, school_id))).scalars().all()
    checkpoints = {(r.enrollment_id, r.subject, r.grading_period): r for r in stored}
    checkpoints.update({(r.enrollment_id, r.subject, r.grading_period): r for r in rows
                        if r.school_id == school_id and r.grading_period in (1, 2)})
    out = []
    for enrollment_id, subject in sorted(wanted):
        first = checkpoints.get((enrollment_id, subject, 1))
        if not ml_client.first_term_ready(first):
            continue
        second = checkpoints.get((enrollment_id, subject, 2))
        if not ml_client.term_ready(second, 2):
            second = None
        out.append((first, second))
    return out
