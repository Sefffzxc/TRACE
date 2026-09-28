"""Staff directory, own profile, and the administrator's approval queue."""
from __future__ import annotations

import os
import pathlib
import secrets
import uuid
from datetime import datetime, timezone
from typing import List, Optional

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

from app.api.deps import get_current_admin, get_current_user
from app.core.config import settings
from app.core.security import (
    generate_temp_password,
    get_password_hash,
    verify_password,
)
from app.db.session import get_db
from app.models.academic import Section, TeacherAssignment
from app.models.staff import (
    ROLE_TEACHER,
    STATUS_APPROVED,
    STATUS_DEACTIVATED,
    STATUS_PENDING,
    STATUS_REJECTED,
    AuditLog,
    Staff,
)
from app.schemas.staff import (
    AuditLogResponse,
    ChangePasswordRequest,
    StaffAdminUpdate,
    StaffResponse,
    StaffUpdateProfile,
)
from app.services.audit import Action, log_event

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
    if not verify_password(payload.current_password, current.password_hash):
        raise HTTPException(status_code=400, detail="Your current password is incorrect.")
    if verify_password(payload.new_password, current.password_hash):
        raise HTTPException(
            status_code=400, detail="The new password must be different from the old one."
        )
    current.password_hash = get_password_hash(payload.new_password)
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
    db: AsyncSession = Depends(get_db),
):
    """Everyone can read the teacher directory; only admins see admins."""
    stmt = select(Staff)
    if role:
        stmt = stmt.where(Staff.role == role)
    elif current.role != "admin":
        stmt = stmt.where(Staff.role == ROLE_TEACHER)
    if account_status:
        stmt = stmt.where(Staff.account_status == account_status)
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
    current: Staff = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    """Teacher directory enriched with the sections each one handles."""
    teachers = (
        await db.execute(
            select(Staff)
            .where(Staff.role == ROLE_TEACHER, Staff.is_approved.is_(True))
            .order_by(Staff.last_name, Staff.first_name)
        )
    ).scalars().all()

    assignments = (
        await db.execute(
            select(TeacherAssignment, Section).join(
                Section, Section.section_id == TeacherAssignment.section_id
            )
        )
    ).all()

    by_teacher: dict[uuid.UUID, list[str]] = {}
    adviser_of: dict[uuid.UUID, str] = {}
    for assignment, section in assignments:
        by_teacher.setdefault(assignment.teacher_id, []).append(section.name)
        if assignment.is_adviser:
            adviser_of[assignment.teacher_id] = section.name

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
    _: Staff = Depends(get_current_admin), db: AsyncSession = Depends(get_db)
):
    rows = (
        await db.execute(
            select(Staff)
            .where(Staff.account_status == STATUS_PENDING)
            .order_by(Staff.created_at.desc())
        )
    ).scalars().all()
    return [StaffResponse.model_validate(r) for r in rows]


@router.get("/api/staff/audit-logs", response_model=List[AuditLogResponse])
async def list_audit_logs(
    limit: int = Query(default=100, le=500),
    action: Optional[str] = None,
    _: Staff = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    logs = (await db.execute(stmt)).scalars().all()

    ids = {log.admin_id for log in logs} | {log.target_user_id for log in logs}
    ids.discard(None)
    names: dict[uuid.UUID, str] = {}
    if ids:
        people = (
            await db.execute(select(Staff).where(Staff.id.in_(ids)))
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
    _: Staff = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    staff = (
        await db.execute(select(Staff).where(Staff.id == staff_id))
    ).scalar_one_or_none()
    if staff is None:
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
    db: AsyncSession = Depends(get_db),
):
    staff = (
        await db.execute(select(Staff).where(Staff.id == staff_id))
    ).scalar_one_or_none()
    if staff is None:
        raise HTTPException(status_code=404, detail="Staff member not found.")
    if staff.id == admin.id and payload.action in {"deactivate", "reject"}:
        raise HTTPException(
            status_code=400, detail="You cannot deactivate your own account."
        )

    action = payload.action
    response: dict = {}

    if action == "approve":
        staff.is_approved = True
        staff.account_status = STATUS_APPROVED
        staff.approved_at = _now()
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
        staff.is_approved = False
        staff.account_status = STATUS_REJECTED
        staff.rejection_reason = payload.reason or "No reason given."
        await log_event(
            db, Action.REJECT_USER, actor_id=admin.id, target_user_id=staff.id,
            detail=staff.rejection_reason, request=request,
        )
        response = {"message": f"Registration for {staff.full_name} rejected."}

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
        staff.password_hash = get_password_hash(temp_password)
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

    await db.commit()
    await db.refresh(staff)
    response["user"] = StaffResponse.model_validate(staff).model_dump(mode="json")
    return response


@router.delete("/api/staff/{staff_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_staff(
    staff_id: uuid.UUID,
    request: Request,
    admin: Staff = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    staff = (
        await db.execute(select(Staff).where(Staff.id == staff_id))
    ).scalar_one_or_none()
    if staff is None:
        raise HTTPException(status_code=404, detail="Staff member not found.")
    if staff.id == admin.id:
        raise HTTPException(status_code=400, detail="You cannot delete your own account.")
    await log_event(
        db, Action.DEACTIVATE_USER, actor_id=admin.id, detail=f"deleted {staff.email}",
        request=request,
    )
    await db.delete(staff)
    await db.commit()
