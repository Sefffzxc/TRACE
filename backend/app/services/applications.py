"""Application history, eligibility and school-change boundaries."""
from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import Base
from app.models.applications import ApplicationEvent, ResubmissionChallenge
from app.models.schools import SCHOOL_REJECTED, School
from app.models.staff import AuditLog, ROLE_ADMIN, ROLE_TEACHER, STATUS_REJECTED, Staff


def now_utc():
    return datetime.now(timezone.utc)


def aware(value):
    return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value


def application_eligible(staff: Staff | None, school: School | None) -> bool:
    if staff is None or staff.account_status != STATUS_REJECTED or not staff.is_active:
        return False
    if staff.role == ROLE_TEACHER:
        # A teacher can correct their details even if the selected school is no
        # longer approved; resubmitting still needs an approved destination.
        return school is not None
    return staff.role == ROLE_ADMIN and school is not None and school.status == SCHOOL_REJECTED


def application_state_digest(staff: Staff, school: School | None) -> str:
    """An earlier rejection's token cannot authorize a later account state."""
    values = [str(staff.id), staff.email, staff.role, staff.school_id,
              staff.account_status, staff.is_active, staff.password_hash,
              staff.rejection_reason, str(aware(staff.updated_at)),
              school.status if school else None,
              str(aware(school.updated_at)) if school and staff.role == ROLE_ADMIN else None]
    return hmac.new(settings.SECRET_KEY.encode(), json.dumps(values).encode(), hashlib.sha256).hexdigest()


def verification_digest(challenge_id, code: str) -> str:
    return hmac.new(settings.SECRET_KEY.encode(), f"resubmission:{challenge_id}:{code}".encode(), hashlib.sha256).hexdigest()


def correction_token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def ensure_legacy_rejection_event(db: AsyncSession, staff: Staff) -> None:
    """Preserve the current legacy reason before a first correction clears it."""
    if staff.account_status != STATUS_REJECTED:
        return
    existing = await db.scalar(select(ApplicationEvent.id).where(ApplicationEvent.staff_id == staff.id).limit(1))
    if existing is None:
        mapping = {
            "register": ("submitted", "pending"), "school_registered": ("submitted", "pending"),
            "approve_user": ("approve", "approved"), "school_approved": ("approve", "approved"),
            "reject_user": ("reject", "rejected"), "school_rejected": ("reject", "rejected"),
            "reactivate_user": ("reactivate", "approved"), "school_reactivated": ("reactivate", "approved"),
            "deactivate_user": ("deactivate", "deactivated"), "school_deactivated": ("deactivate", "deactivated"),
        }
        logs = (await db.scalars(select(AuditLog).where(
            AuditLog.target_user_id == staff.id, AuditLog.action.in_(mapping),
        ).order_by(AuditLog.created_at, AuditLog.id))).all()
        previous = None
        for entry in logs:
            action, status = mapping[entry.action]
            reason = entry.detail if action == "reject" else None
            db.add(ApplicationEvent(
                staff_id=staff.id, school_id=staff.school_id, actor_id=entry.admin_id,
                action=action, from_status=previous, to_status=status, reason=reason,
                detail="Imported from the existing audit trail.", created_at=aware(entry.created_at) or now_utc(),
            ))
            previous = status
        if previous == STATUS_REJECTED:
            await db.flush()
            return
        db.add(ApplicationEvent(
            staff_id=staff.id, school_id=staff.school_id, actor_id=None,
            action="reject", from_status=None, to_status=STATUS_REJECTED,
            reason=staff.rejection_reason,
            detail="Rejection recorded before detailed application history was introduced.",
            created_at=aware(staff.updated_at) or now_utc(),
        ))
        await db.flush()


async def record_application_event(
    db: AsyncSession, staff: Staff, action: str, from_status: str | None,
    to_status: str | None, actor_id=None, reason: str | None = None,
    detail: str | None = None,
) -> ApplicationEvent:
    # Flush account changes before invalidating tokens, so every decision uses
    # the same account-then-challenge lock order as applicant corrections.
    await db.flush()
    await db.execute(update(ResubmissionChallenge).where(
        ResubmissionChallenge.staff_id == staff.id,
        ResubmissionChallenge.used_at.is_(None),
    ).values(used_at=now_utc()))
    event = ApplicationEvent(
        staff_id=staff.id, school_id=staff.school_id, actor_id=actor_id,
        action=action, from_status=from_status, to_status=to_status,
        reason=reason, detail=detail,
    )
    db.add(event)
    await db.flush()
    return event


async def can_change_application_school(db: AsyncSession, staff: Staff) -> bool:
    if staff.role != ROLE_TEACHER or staff.approved_at is not None or staff.last_login_at is not None:
        return False
    approved = await db.scalar(select(ApplicationEvent.id).where(
        ApplicationEvent.staff_id == staff.id,
        or_(ApplicationEvent.to_status.in_(["approved", "deactivated"]),
            ApplicationEvent.from_status.in_(["approved", "deactivated"])),
    ).limit(1))
    if approved is not None:
        return False
    prior_approval = await db.scalar(select(AuditLog.id).where(
        AuditLog.target_user_id == staff.id,
        AuditLog.action.in_(["approve_user", "reactivate_user", "deactivate_user"]),
    ).limit(1))
    if prior_approval is not None:
        return False
    # Inspect actual staff foreign keys so newly added teaching/record tables
    # also prevent a self-service move. Authentication requests and audit history
    # describe an application and are safe to retain under the same account.
    ignored = {"audit_logs", "application_events", "resubmission_challenges", "otp_codes", "password_resets"}
    for table in Base.metadata.sorted_tables:
        if table.name in ignored:
            continue
        columns = [column for column in table.columns
                   if any(key.target_fullname == "staff.id" for key in column.foreign_keys)]
        if columns and await db.scalar(select(table.c[next(iter(table.primary_key)).name]).where(
            or_(*(column == staff.id for column in columns)),
        ).limit(1)) is not None:
            return False
    return True
