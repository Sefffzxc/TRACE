"""Staff directory, own profile, and the administrator's approval queue."""
from __future__ import annotations

import os
import pathlib
import secrets
import uuid
from datetime import datetime, timezone
from typing import List, Literal, Optional

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_admin, get_current_user, school_scope
from app.core.config import settings
from app.core.security import (
    generate_temp_password,
    async_get_password_hash,
    async_verify_password,
)
from app.db.session import get_db
from app.models.academic import Section, SectionAdviser, TeacherAssignment
from app.models.applications import ApplicationEvent
from app.models.schools import School
from app.models.staff import (
    ROLE_ADMIN,
    ROLE_SUPER_ADMIN,
    ROLE_TEACHER,
    STATUS_APPROVED,
    STATUS_BLOCKED,
    STATUS_DEACTIVATED,
    STATUS_PENDING,
    STATUS_REJECTED,
    AuditLog,
    Staff,
)
from app.schemas.staff import (
    ApplicationHistoryResponse,
    AuditLogResponse,
    ChangePasswordRequest,
    StaffAdminUpdate,
    StaffResponse,
    StaffUpdateProfile,
)
from app.services.audit import Action, log_event
from app.services.applications import ensure_legacy_rejection_event, record_application_event
from app.services.email import EmailDeliveryError, send_account_decision_email

router = APIRouter(tags=["Staff"])

ALLOWED_IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}
EXTENSION_FOR = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ══════════════════════════════════════════════════════════════════════════════
# Own profile
# ══════════════════════════════════════════════════════════════════════════════
@router.get("/api/staff/me", response_model=StaffResponse)
async def read_me(current: Staff = Depends(get_current_user)):
    return StaffResponse.model_validate(current)


@router.patch("/api/staff/me", response_model=StaffResponse)
async def update_me(
    payload: StaffUpdateProfile,
    current: Staff = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    for field, value in payload.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(current, field, value)
    await db.commit()
    await db.refresh(current)
    return StaffResponse.model_validate(current)


@router.post("/api/staff/me/password")
async def change_own_password(
    payload: ChangePasswordRequest,
    request: Request,
    current: Staff = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not await async_verify_password(payload.current_password, current.password_hash):
        raise HTTPException(status_code=400, detail="Your current password is incorrect.")
    if await async_verify_password(payload.new_password, current.password_hash):
        raise HTTPException(
            status_code=400, detail="The new password must be different from the old one."
        )
    current.password_hash = await async_get_password_hash(payload.new_password)
    current.requires_password_change = False
    await log_event(
        db, Action.PASSWORD_CHANGED, actor_id=current.id, target_user_id=current.id,
        request=request,
    )
    await db.commit()
    return {"message": "Password updated."}


@router.post("/api/staff/me/photo", response_model=StaffResponse)
async def upload_photo(
    file: UploadFile = File(...),
    current: Staff = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if file.content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            status_code=400, detail="Please upload a PNG, JPG, WEBP or GIF image."
        )

    contents = await file.read()
    if len(contents) > settings.MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Image is too large (max {settings.MAX_UPLOAD_BYTES // (1024 * 1024)} MB).",
        )

    folder = pathlib.Path(settings.UPLOAD_DIR) / "profile_pics"
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{secrets.token_hex(16)}{EXTENSION_FOR[file.content_type]}"
    (folder / name).write_bytes(contents)

    # Remove the previous file so uploads do not pile up.
    if current.profile_pic:
        old = pathlib.Path(settings.UPLOAD_DIR) / current.profile_pic.replace(
            "/uploads/", ""
        )
        if old.is_file():
            try:
                old.unlink()
            except OSError:
                pass

    current.profile_pic = f"/uploads/profile_pics/{name}"
    await db.commit()
    await db.refresh(current)
    return StaffResponse.model_validate(current)


@router.delete("/api/staff/me/photo", response_model=StaffResponse)
async def remove_photo(
    current: Staff = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    if current.profile_pic:
        path = pathlib.Path(settings.UPLOAD_DIR) / current.profile_pic.replace(
            "/uploads/", ""
        )
        if path.is_file():
            try:
                path.unlink()
            except OSError:
                pass
    current.profile_pic = None
    await db.commit()
    await db.refresh(current)
    return StaffResponse.model_validate(current)


# ══════════════════════════════════════════════════════════════════════════════
# Directory
# ══════════════════════════════════════════════════════════════════════════════
@router.get("/api/staff", response_model=List[StaffResponse])
async def list_staff(
    role: Optional[str] = Query(default=None),
    account_status: Optional[str] = Query(default=None),
    search: Optional[str] = Query(default=None),
    current: Staff = Depends(get_current_user),
    scope: int = Depends(school_scope),
    db: AsyncSession = Depends(get_db),
):
    """Everyone can read the teacher directory; only admins see admins.

    Scoped to the caller's own school. The super admin's ``school_id`` is NULL,
    so this filter can never return the operator account either.
    """
    stmt = select(Staff).where(Staff.school_id == scope)
    if account_status in {STATUS_REJECTED, STATUS_BLOCKED} and current.role != ROLE_ADMIN:
        raise HTTPException(status_code=403, detail="Administrator access is required to review applications.")
    if role:
        stmt = stmt.where(Staff.role == role)
    elif current.role != "admin":
        stmt = stmt.where(Staff.role == ROLE_TEACHER)
    if account_status:
        stmt = stmt.where(Staff.account_status == account_status)
    else:
        # Retain rejected registrations for their decision and audit history,
        # while excluding them from the ordinary staff directory.
        stmt = stmt.where(Staff.account_status.notin_([STATUS_REJECTED, STATUS_BLOCKED]))
    if search:
        pattern = f"%{search.lower()}%"
        stmt = stmt.where(
            func.lower(Staff.first_name).like(pattern)
            | func.lower(Staff.last_name).like(pattern)
            | func.lower(Staff.email).like(pattern)
        )
    stmt = stmt.order_by(Staff.last_name, Staff.first_name)
    rows = (await db.execute(stmt)).scalars().all()
    return [StaffResponse.model_validate(r) for r in rows]


@router.get("/api/staff/teachers")
async def list_teachers(
    current: Staff = Depends(get_current_user),
    scope: int = Depends(school_scope),
    db: AsyncSession = Depends(get_db),
):
    """Teacher directory enriched with the sections each one handles."""
    teachers = (
        await db.execute(
            select(Staff)
            .where(
                Staff.school_id == scope,
                Staff.role == ROLE_TEACHER,
                Staff.is_approved.is_(True),
                Staff.account_status.notin_([STATUS_REJECTED, STATUS_BLOCKED]),
            )
            .order_by(Staff.last_name, Staff.first_name)
        )
    ).scalars().all()

    assignments = (
        await db.execute(
            select(TeacherAssignment, Section)
            .join(Section, Section.section_id == TeacherAssignment.section_id)
            .where(
                TeacherAssignment.school_id == scope,
                Section.school_id == scope,
            )
        )
    ).all()

    by_teacher: dict[uuid.UUID, list[str]] = {}
    adviser_of: dict[uuid.UUID, str] = {}
    for assignment, section in assignments:
        if assignment.subject_id is not None or not assignment.is_adviser:
            by_teacher.setdefault(assignment.teacher_id, []).append(section.name)
    advisers = (await db.execute(
        select(SectionAdviser, Section)
        .join(Section, Section.section_id == SectionAdviser.section_id)
        .where(SectionAdviser.school_id == scope, Section.school_id == scope)
    )).all()
    for adviser, section in advisers:
        adviser_of[adviser.teacher_id] = section.name
        by_teacher.setdefault(adviser.teacher_id, []).append(section.name)

    out = []
    for teacher in teachers:
        data = StaffResponse.model_validate(teacher).model_dump()
        data["full_name"] = teacher.full_name
        data["sections"] = sorted(set(by_teacher.get(teacher.id, [])))
        data["advisory_section"] = adviser_of.get(teacher.id)
        out.append(data)
    return out


@router.get("/api/staff/pending", response_model=List[StaffResponse])
async def list_pending(
    _: Staff = Depends(get_current_admin),
    scope: int = Depends(school_scope),
    db: AsyncSession = Depends(get_db),
):
    rows = (
        await db.execute(
            select(Staff)
            .where(
                Staff.school_id == scope,
                Staff.account_status == STATUS_PENDING,
                Staff.role == ROLE_TEACHER,
            )
            .order_by(Staff.created_at.desc())
        )
    ).scalars().all()
    return [StaffResponse.model_validate(r) for r in rows]


@router.get("/api/staff/applications", response_model=List[StaffResponse])
async def list_applications(
    status_filter: Literal["pending", "rejected", "blocked"] = Query(default="pending", alias="status"),
    _: Staff = Depends(get_current_admin),
    scope: int = Depends(school_scope),
    db: AsyncSession = Depends(get_db),
):
    rows = (
        await db.execute(
            select(Staff)
            .where(
                Staff.school_id == scope,
                Staff.role == ROLE_TEACHER,
                Staff.account_status == status_filter,
            )
            .order_by(Staff.updated_at.desc(), Staff.created_at.desc())
        )
    ).scalars().all()
    return [StaffResponse.model_validate(row) for row in rows]


@router.get("/api/staff/{staff_id}/application-history", response_model=List[ApplicationHistoryResponse])
async def application_history(
    staff_id: uuid.UUID,
    _: Staff = Depends(get_current_admin),
    scope: int = Depends(school_scope),
    db: AsyncSession = Depends(get_db),
):
    # History may import legacy events. Their school foreign key must follow
    # the same school-then-applicant lock order as corrections and decisions.
    await db.execute(select(School.school_id).where(School.school_id == scope).with_for_update())
    person = (
        await db.execute(
            select(Staff).where(
                Staff.id == staff_id, Staff.school_id == scope, Staff.role == ROLE_TEACHER,
            ).with_for_update()
        )
    ).scalar_one_or_none()
    if person is None:
        raise HTTPException(status_code=404, detail="Staff member not found.")
    await ensure_legacy_rejection_event(db, person)
    await db.commit()
    events = (
        await db.execute(
            select(ApplicationEvent).where(
                ApplicationEvent.staff_id == person.id, ApplicationEvent.school_id == scope,
            ).order_by(ApplicationEvent.created_at.desc(), ApplicationEvent.id.desc())
        )
    ).scalars().all()
    actor_ids = {event.actor_id for event in events if event.actor_id is not None}
    names = {}
    if actor_ids:
        actors = (
            await db.execute(select(Staff).where(Staff.id.in_(actor_ids), Staff.school_id == scope))
        ).scalars().all()
        names = {actor.id: actor.full_name for actor in actors}
    output = []
    for event in events:
        item = ApplicationHistoryResponse.model_validate(event)
        item.actor_name = names.get(event.actor_id)
        output.append(item)
    return output


@router.get("/api/staff/audit-logs", response_model=List[AuditLogResponse])
async def list_audit_logs(
    limit: int = Query(default=100, le=500),
    action: Optional[str] = None,
    _: Staff = Depends(get_current_admin),
    scope: int = Depends(school_scope),
    db: AsyncSession = Depends(get_db),
):
    # audit_logs has no school_id column, so the trail is scoped through the
    # actor: an administrator sees only what their own school's staff did. The
    # super admin's school_id is NULL, so their actions are excluded too.
    school_actors = select(Staff.id).where(Staff.school_id == scope)
    stmt = (
        select(AuditLog)
        .where(AuditLog.admin_id.in_(school_actors))
        .order_by(AuditLog.created_at.desc())
        .limit(limit)
    )
    if action:
        stmt = stmt.where(AuditLog.action == action)
    logs = (await db.execute(stmt)).scalars().all()

    ids = {log.admin_id for log in logs} | {log.target_user_id for log in logs}
    ids.discard(None)
    names: dict[uuid.UUID, str] = {}
    if ids:
        people = (
            await db.execute(
                select(Staff).where(Staff.school_id == scope, Staff.id.in_(ids))
            )
        ).scalars().all()
        names = {p.id: p.full_name for p in people}

    out = []
    for log in logs:
        item = AuditLogResponse.model_validate(log)
        item.admin_name = names.get(log.admin_id)
        item.target_name = names.get(log.target_user_id)
        out.append(item)
    return out


@router.get("/api/staff/{staff_id}", response_model=StaffResponse)
async def read_staff(
    staff_id: uuid.UUID,
    current: Staff = Depends(get_current_user),
    scope: int = Depends(school_scope),
    db: AsyncSession = Depends(get_db),
):
    """One colleague's profile — from the caller's own school only.

    Reading an arbitrary staff id used to be allowed to any signed-in user,
    which was harmless with one school and a cross-tenant leak with many. A
    record outside the caller's school answers 404, the same as one that does
    not exist, so ids cannot be probed.
    """
    staff = (
        await db.execute(
            select(Staff).where(Staff.id == staff_id, Staff.school_id == scope)
        )
    ).scalar_one_or_none()
    if staff is None or staff.role == ROLE_SUPER_ADMIN:
        raise HTTPException(status_code=404, detail="Staff member not found.")
    if staff.account_status in {STATUS_REJECTED, STATUS_BLOCKED} and current.role != ROLE_ADMIN:
        raise HTTPException(status_code=404, detail="Staff member not found.")
    return StaffResponse.model_validate(staff)


# ══════════════════════════════════════════════════════════════════════════════
# Admin actions
# ══════════════════════════════════════════════════════════════════════════════
@router.patch("/api/staff/{staff_id}")
async def admin_update_staff(
    staff_id: uuid.UUID,
    payload: StaffAdminUpdate,
    request: Request,
    admin: Staff = Depends(get_current_admin),
    scope: int = Depends(school_scope),
    db: AsyncSession = Depends(get_db),
):
    await db.execute(select(School.school_id).where(School.school_id == scope).with_for_update())
    # Scoped lookup: a staff member in another school is reported as simply not
    # existing, so a principal cannot discover other schools' accounts by id.
    staff = (
        await db.execute(
            select(Staff).where(Staff.id == staff_id, Staff.school_id == scope).with_for_update()
        )
    ).scalar_one_or_none()
    if staff is None or staff.role == ROLE_SUPER_ADMIN:
        raise HTTPException(status_code=404, detail="Staff member not found.")
    if staff.id == admin.id and payload.action in {"deactivate", "reject", "reopen", "block"}:
        raise HTTPException(
            status_code=400, detail="You cannot change your own application status."
        )
    if staff.role != ROLE_TEACHER and not (
        staff.id == admin.id and payload.action in {"unlock", "force_reset"}
    ):
        raise HTTPException(
            status_code=403,
            detail="Principal applications must be managed by the system operator.",
        )
    if payload.role and payload.role != ROLE_TEACHER:
        raise HTTPException(status_code=403, detail="A teacher application cannot grant administrator access.")

    action = payload.action
    previous_status = staff.account_status
    response: dict = {}
    if previous_status == STATUS_BLOCKED and action not in {"reopen", "block", "unlock", "force_reset"}:
        raise HTTPException(status_code=409, detail="Reopen the blocked application before making another decision.")
    if action == "reopen" and previous_status not in {STATUS_REJECTED, STATUS_BLOCKED}:
        raise HTTPException(status_code=409, detail="Only rejected or blocked applications can be reopened.")
    await ensure_legacy_rejection_event(db, staff)

    if action == "approve":
        staff.is_active = True
        staff.is_approved = True
        staff.account_status = STATUS_APPROVED
        staff.approved_at = staff.approved_at or _now()
        staff.approved_by = admin.id
        staff.rejection_reason = None
        if payload.role:
            staff.role = payload.role
        await log_event(
            db, Action.APPROVE_USER, actor_id=admin.id, target_user_id=staff.id,
            request=request,
        )
        response = {"message": f"{staff.full_name} approved."}

    elif action == "reject":
        staff.is_active = True
        staff.is_approved = False
        staff.account_status = STATUS_REJECTED
        staff.rejection_reason = payload.reason
        await log_event(
            db, Action.REJECT_USER, actor_id=admin.id, target_user_id=staff.id,
            detail=staff.rejection_reason, request=request,
        )
        response = {"message": f"Registration for {staff.full_name} rejected."}

    elif action == "reopen":
        staff.account_status = STATUS_PENDING
        staff.is_approved = False
        staff.is_active = True
        staff.rejection_reason = None
        await log_event(
            db, "reopen_application", actor_id=admin.id, target_user_id=staff.id,
            detail=payload.reason, request=request,
        )
        response = {"message": f"Application for {staff.full_name} reopened for review."}

    elif action == "block":
        staff.account_status = STATUS_BLOCKED
        staff.is_approved = False
        staff.is_active = False
        staff.rejection_reason = payload.reason
        await log_event(
            db, "block_application", actor_id=admin.id, target_user_id=staff.id,
            detail=payload.reason, request=request,
        )
        response = {"message": f"Application for {staff.full_name} blocked."}

    elif action == "deactivate":
        staff.is_active = False
        staff.account_status = STATUS_DEACTIVATED
        await log_event(
            db, Action.DEACTIVATE_USER, actor_id=admin.id, target_user_id=staff.id,
            detail=payload.reason, request=request,
        )
        response = {"message": f"{staff.full_name} deactivated."}

    elif action == "reactivate":
        staff.is_active = True
        staff.account_status = STATUS_APPROVED
        staff.is_approved = True
        staff.rejection_reason = None
        if previous_status in {STATUS_PENDING, STATUS_REJECTED}:
            staff.approved_at = staff.approved_at or _now()
            staff.approved_by = admin.id
        await log_event(
            db, Action.REACTIVATE_USER, actor_id=admin.id, target_user_id=staff.id,
            request=request,
        )
        response = {"message": f"{staff.full_name} reactivated."}

    elif action == "unlock":
        staff.permanent_lock = False
        staff.locked_until = None
        staff.login_attempts = 0
        staff.lockout_count = 0
        await log_event(
            db, Action.ACCOUNT_UNLOCKED, actor_id=admin.id, target_user_id=staff.id,
            request=request,
        )
        response = {"message": f"{staff.full_name} unlocked."}

    elif action == "force_reset":
        temp_password = generate_temp_password()
        staff.password_hash = await async_get_password_hash(temp_password)
        staff.requires_password_change = True
        staff.login_attempts = 0
        staff.locked_until = None
        staff.lockout_count = 0
        staff.permanent_lock = False
        await log_event(
            db, Action.FORCE_RESET_PASSWORD, actor_id=admin.id, target_user_id=staff.id,
            request=request,
        )
        response = {
            "message": "Temporary password issued. Share it with the staff member "
            "securely — they must change it at next sign-in.",
            "temporary_password": temp_password,
        }

    if action in {"approve", "reject", "reopen", "block", "deactivate", "reactivate"}:
        await record_application_event(
            db, staff, action=action, from_status=previous_status,
            to_status=staff.account_status, actor_id=admin.id, reason=payload.reason,
        )

    if action in {"approve", "reject", "reopen", "block"} or (
        action == "reactivate" and previous_status in {STATUS_PENDING, STATUS_REJECTED}
    ):
        school = await db.get(School, staff.school_id) if staff.school_id is not None else None
        try:
            await send_account_decision_email(
                staff.email, full_name=staff.full_name, role=staff.role,
                decision={"reject": "rejected", "reopen": "reopened", "block": "blocked"}.get(action, "approved"),
                school_name=school.name if school is not None else None,
                reason=staff.rejection_reason if action in {"reject", "block"} else payload.reason,
            )
        except EmailDeliveryError:
            await db.rollback()
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="The account decision was not saved because the notification "
                       "email could not be sent. Please try again later.",
            ) from None

    await db.commit()
    await db.refresh(staff)
    response["user"] = StaffResponse.model_validate(staff).model_dump(mode="json")
    return response


@router.delete("/api/staff/{staff_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_staff(
    staff_id: uuid.UUID,
    request: Request,
    admin: Staff = Depends(get_current_admin),
    scope: int = Depends(school_scope),
    db: AsyncSession = Depends(get_db),
):
    staff = (
        await db.execute(
            select(Staff).where(Staff.id == staff_id, Staff.school_id == scope)
        )
    ).scalar_one_or_none()
    if staff is None or staff.role == ROLE_SUPER_ADMIN:
        raise HTTPException(status_code=404, detail="Staff member not found.")
    if staff.role != ROLE_TEACHER:
        raise HTTPException(status_code=403, detail="Principal accounts must be managed by the system operator.")
    if staff.id == admin.id:
        raise HTTPException(status_code=400, detail="You cannot delete your own account.")
    await log_event(
        db, Action.DEACTIVATE_USER, actor_id=admin.id, detail=f"deleted {staff.email}",
        request=request,
    )
    await db.delete(staff)
    await db.commit()
