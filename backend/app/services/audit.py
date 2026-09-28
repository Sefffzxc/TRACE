"""Helpers for writing the audit trail."""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.staff import AuditLog


class Action:
    REGISTER = "register"
    LOGIN_SUCCESS = "login_success"
    LOGIN_FAILED = "login_failed"
    LOGOUT = "logout"
    OTP_ISSUED = "otp_issued"
    OTP_VERIFIED = "otp_verified"
    OTP_FAILED = "otp_failed"
    ACCOUNT_LOCKED = "account_locked"
    ACCOUNT_PERM_LOCK = "account_perm_lock"
    ACCOUNT_UNLOCKED = "account_unlocked"
    APPROVE_USER = "approve_user"
    REJECT_USER = "reject_user"
    DEACTIVATE_USER = "deactivate_user"
    REACTIVATE_USER = "reactivate_user"
    FORCE_RESET_PASSWORD = "force_reset_password"
    PASSWORD_CHANGED = "password_changed"
    PASSWORD_RESET_REQUESTED = "password_reset_requested"
    PASSWORD_RESET_COMPLETED = "password_reset_completed"
    STUDENT_CREATED = "student_created"
    STUDENT_UPDATED = "student_updated"
    STUDENT_DELETED = "student_deleted"
    PACE_RECORDED = "pace_recorded"


def client_ip(request: Optional[Request]) -> Optional[str]:
    if request is None:
        return None
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


async def log_event(
    db: AsyncSession,
    action: str,
    *,
    actor_id: Optional[uuid.UUID] = None,
    target_user_id: Optional[uuid.UUID] = None,
    detail: Optional[str] = None,
    request: Optional[Request] = None,
    commit: bool = False,
) -> AuditLog:
    entry = AuditLog(
        admin_id=actor_id,
        target_user_id=target_user_id,
        action=action,
        detail=detail,
        ip_address=client_ip(request),
    )
    db.add(entry)
    if commit:
        await db.commit()
    else:
        await db.flush()
    return entry
