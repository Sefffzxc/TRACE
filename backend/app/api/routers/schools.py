"""School registration, operator approval, and a school's own settings.

Three audiences, three sets of rules:

* **The public** may register a school and may list approved schools by name
  and city, which is what a teacher needs to find theirs when signing up.
* **The system operator** (super admin) approves, rejects, suspends and
  restores schools, and sees how many staff and students each has. Counts
  only — there is no route here, or anywhere, that lets them open a student's
  record. See `school_scope` in app/api/deps.py for where that is enforced.
* **A principal** edits their own school's settings: what they call their
  lesson units, whether they track them at all, the passing mark.

The approval chain is the same mechanism at two levels — the operator approves
a school the way a principal approves a teacher — rather than two mechanisms
that have to be learned separately.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    get_current_user,
    get_super_admin,
    limit_register,
    school_admin_scope,
)
from app.core.security import get_password_hash
from app.db.session import get_db
from app.models.schools import (
    SCHOOL_APPROVED,
    SCHOOL_DEACTIVATED,
    SCHOOL_PENDING,
    SCHOOL_REJECTED,
    School,
)
from app.models.staff import (
    ROLE_ADMIN,
    STATUS_APPROVED,
    STATUS_PENDING,
    Staff,
)
from app.models.students import Student
from app.schemas.schools import (
    SchoolAdminView,
    SchoolDecision,
    SchoolDirectoryEntry,
    SchoolRegisterRequest,
    SchoolResponse,
    SchoolUpdate,
)
from app.services.audit import Action, log_event

router = APIRouter(prefix="/api/schools", tags=["Schools"])


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ══════════════════════════════════════════════════════════════════════════════
# Public
# ══════════════════════════════════════════════════════════════════════════════
@router.get("/directory", response_model=List[SchoolDirectoryEntry])
async def school_directory(
    search: Optional[str] = Query(default=None, max_length=100),
    db: AsyncSession = Depends(get_db),
):
    """Approved schools, for the dropdown on the teacher sign-up form.

    Unauthenticated by necessity — a teacher has to pick their school before
    they have an account. It returns only a school's name, city and type, all
    of which are public facts about a school, and only for schools the
    operator has approved. Pending and rejected schools are invisible.
    """
    stmt = (
        select(School)
        .where(School.status == SCHOOL_APPROVED)
        .order_by(School.name)
    )
    if search:
        term = f"%{search.strip()}%"
        stmt = stmt.where(School.name.ilike(term) | School.city.ilike(term))
    rows = (await db.execute(stmt)).scalars().all()
    return [
        SchoolDirectoryEntry(
            school_id=row.school_id,
            name=row.name,
            city=row.city,
            school_type=row.school_type,
        )
        for row in rows
    ]


@router.post("/register", status_code=201, dependencies=[Depends(limit_register)])
async def register_school(
    payload: SchoolRegisterRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """A school head registers their school and their own account together.

    Both are created pending. Nothing is usable until the operator approves,
    which is what stops anyone from creating a school and walking straight in.
    """
    email = str(payload.email).lower()
    existing = (
        await db.execute(select(Staff).where(Staff.email == email))
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with that email address already exists.",
        )

    # Same name in the same city is almost certainly a duplicate request.
    # Different city is almost certainly a different school, so that is allowed.
    clash = (
        await db.execute(
            select(School).where(
                func.lower(School.name) == payload.school.name.lower(),
                func.lower(School.city) == payload.school.city.lower(),
                School.status.in_([SCHOOL_PENDING, SCHOOL_APPROVED]),
            )
        )
    ).scalar_one_or_none()
    if clash is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"{payload.school.name} in {payload.school.city} is already "
                "registered or awaiting approval. If this is your school, ask "
                "its administrator to add you as a teacher instead."
            ),
        )

    school = School(**payload.school.model_dump(), status=SCHOOL_PENDING)
    db.add(school)
    await db.flush()  # assigns school_id for the principal below

    principal = Staff(
        school_id=school.school_id,
        email=email,
        password_hash=get_password_hash(payload.password),
        first_name=payload.first_name.strip(),
        middle_name=(payload.middle_name or "").strip() or None,
        last_name=payload.last_name.strip(),
        contact_number=payload.contact_number.strip(),
        role=ROLE_ADMIN,
        employee_no=payload.employee_no,
        department="Administration",
        account_status=STATUS_PENDING,
        is_approved=False,
    )
    db.add(principal)
    await db.flush()

    await log_event(
        db,
        Action.SCHOOL_REGISTERED,
        target_user_id=principal.id,
        detail=f"{school.name} ({school.city}) — awaiting approval",
        request=request,
    )
    await db.commit()

    return {
        "message": (
            f"{school.name} has been registered and is awaiting approval. "
            "You will be able to sign in once the system operator approves it."
        ),
        "school_id": school.school_id,
        "status": SCHOOL_PENDING,
    }


# ══════════════════════════════════════════════════════════════════════════════
# The school's own settings
# ══════════════════════════════════════════════════════════════════════════════
@router.get("/me", response_model=SchoolResponse)
async def my_school(
    current: Staff = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """The caller's own school. Every screen reads this to know what to show —
    whether the lesson-progress pages exist, and what this school calls them."""
    if current.school_id is None:
        raise HTTPException(
            status_code=404,
            detail="This account is not attached to a school.",
        )
    school = await db.get(School, current.school_id)
    if school is None:
        raise HTTPException(status_code=404, detail="School not found.")
    return SchoolResponse.model_validate(school)


@router.patch("/me", response_model=SchoolResponse)
async def update_my_school(
    payload: SchoolUpdate,
    request: Request,
    scope: int = Depends(school_admin_scope),
    db: AsyncSession = Depends(get_db),
):
    """A principal edits their own school. `school_admin_scope` means the
    operator cannot reach this, and no principal can reach another school."""
    school = await db.get(School, scope)
    if school is None:
        raise HTTPException(status_code=404, detail="School not found.")

    data = payload.model_dump(exclude_unset=True)
    for field, value in data.items():
        setattr(school, field, value)
    school.updated_at = _now()

    await log_event(
        db,
        Action.SCHOOL_SETTINGS_UPDATED,
        detail=f"changed: {', '.join(sorted(data)) or 'nothing'}",
        request=request,
    )
    await db.commit()
    await db.refresh(school)
    return SchoolResponse.model_validate(school)


# ══════════════════════════════════════════════════════════════════════════════
# The system operator
# ══════════════════════════════════════════════════════════════════════════════
@router.get("", response_model=List[SchoolAdminView])
async def list_schools(
    status_filter: Optional[str] = Query(default=None, alias="status"),
    _: Staff = Depends(get_super_admin),
    db: AsyncSession = Depends(get_db),
):
    """Every school, for the operator's approvals screen.

    Each row carries two counts and no records. That is the whole of the
    operator's visibility into a school: how big it is, not who is in it.
    """
    stmt = select(School).order_by(School.created_at.desc())
    if status_filter:
        wanted = {s.strip().lower() for s in status_filter.split(",") if s.strip()}
        stmt = stmt.where(School.status.in_(wanted))
    schools = (await db.execute(stmt)).scalars().all()
    if not schools:
        return []

    ids = [s.school_id for s in schools]

    staff_counts = dict(
        (
            await db.execute(
                select(Staff.school_id, func.count(Staff.id))
                .where(Staff.school_id.in_(ids))
                .group_by(Staff.school_id)
            )
        ).all()
    )
    student_counts = dict(
        (
            await db.execute(
                select(Student.school_id, func.count(Student.student_id))
                .where(Student.school_id.in_(ids))
                .group_by(Student.school_id)
            )
        ).all()
    )
    principals = {
        row.school_id: row
        for row in (
            await db.execute(
                select(Staff).where(
                    Staff.school_id.in_(ids), Staff.role == ROLE_ADMIN
                )
            )
        ).scalars()
    }

    out = []
    for school in schools:
        item = SchoolAdminView.model_validate(school)
        principal = principals.get(school.school_id)
        if principal is not None:
            item.principal_name = principal.full_name
            item.principal_email = principal.email
        item.staff_count = int(staff_counts.get(school.school_id, 0))
        item.student_count = int(student_counts.get(school.school_id, 0))
        out.append(item)
    return out


@router.patch("/{school_id}", response_model=SchoolAdminView)
async def decide_school(
    school_id: int,
    payload: SchoolDecision,
    request: Request,
    operator: Staff = Depends(get_super_admin),
    db: AsyncSession = Depends(get_db),
):
    """Approve, reject, suspend or restore a school.

    Approving also activates the principal's account — the two were registered
    together and are released together, so there is no second step where a
    school exists but nobody can sign in to it.
    """
    school = await db.get(School, school_id)
    if school is None:
        raise HTTPException(status_code=404, detail="School not found.")

    principal = (
        await db.execute(
            select(Staff).where(
                Staff.school_id == school_id, Staff.role == ROLE_ADMIN
            )
        )
    ).scalars().first()

    if payload.action == "approve":
        school.status = SCHOOL_APPROVED
        school.rejection_reason = None
        school.approved_at = _now()
        school.approved_by = operator.id
        if principal is not None and not principal.is_approved:
            principal.is_approved = True
            principal.account_status = STATUS_APPROVED
            principal.approved_at = _now()
            principal.approved_by = operator.id

    elif payload.action == "reject":
        if not payload.reason:
            raise HTTPException(
                status_code=400,
                detail="A reason is required so the school knows what to correct.",
            )
        school.status = SCHOOL_REJECTED
        school.rejection_reason = payload.reason

    elif payload.action == "deactivate":
        school.status = SCHOOL_DEACTIVATED
        school.rejection_reason = payload.reason

    elif payload.action == "reactivate":
        school.status = SCHOOL_APPROVED
        school.rejection_reason = None

    school.updated_at = _now()
    await log_event(
        db,
        {
            "approve": Action.SCHOOL_APPROVED,
            "reject": Action.SCHOOL_REJECTED,
            "deactivate": Action.SCHOOL_DEACTIVATED,
            "reactivate": Action.SCHOOL_REACTIVATED,
        }[payload.action],
        actor_id=operator.id,
        target_user_id=principal.id if principal is not None else None,
        detail=f"{school.name} (#{school.school_id})"
               + (f" — {payload.reason}" if payload.reason else ""),
        request=request,
    )
    await db.commit()
    await db.refresh(school)

    item = SchoolAdminView.model_validate(school)
    if principal is not None:
        item.principal_name = principal.full_name
        item.principal_email = principal.email
    return item
