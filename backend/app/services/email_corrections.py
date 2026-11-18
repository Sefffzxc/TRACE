"""Administrator-assisted recovery never grants approval or creates an account.

The administrator proves their password and confirms the applicant's identity;
the recipient proves ownership of the replacement inbox. Password hashing and
email delivery run after committing, so neither holds a database connection.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import math
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError

from app.api.deps import require_account_access
from app.core.config import settings
from app.core.security import async_verify_password, generate_otp
from app.models.applications import ResubmissionChallenge
from app.models.email_corrections import EmailCorrectionChallenge, EmailCorrectionGuard
from app.models.schools import School
from app.models.staff import (
    OTPCode, PasswordReset, ROLE_ADMIN, ROLE_SUPER_ADMIN, ROLE_TEACHER,
    Staff, StaffDevice, StaffSession,
)
from app.services.audit import log_event
from app.services.email import (
    EmailDeliveryError, send_email_correction_code, send_email_correction_notice,
)
from app.services.email_identity import canonical_email, lock_email_identity


log = logging.getLogger("trace.email_correction")
_PASSWORD_WINDOW = 15 * 60
_PASSWORD_LIMIT = 5
_SEND_COOLDOWN = 60
_TARGET_HOURLY_LIMIT = 5
_ACTOR_HOURLY_LIMIT = 20


def now_utc():
    return datetime.now(timezone.utc)


def aware(value):
    return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value


def _error(status, code, message, *, retry_after=None):
    headers = {"Retry-After": str(max(1, math.ceil(retry_after)))} if retry_after is not None else None
    return HTTPException(status, detail={"code": code, "message": message}, headers=headers)


def _expired():
    return _error(410, "email_correction_expired", "This email correction has expired or is no longer valid. Ask the administrator to send a new verification.")


def _limited(seconds):
    return _error(429, "email_correction_rate_limited", "Too many email correction attempts. Please wait before trying again.", retry_after=seconds)


def code_digest(challenge_id, code):
    return hmac.new(settings.SECRET_KEY.encode(), f"email-correction:{challenge_id}:{code}".encode(), hashlib.sha256).hexdigest()


def _state_digest(actor, target, schools):
    def person_state(person):
        return [str(person.id), person.email, person.password_hash, person.role,
                person.school_id, person.account_status, person.is_active,
                person.is_approved, person.permanent_lock, person.requires_password_change,
                str(aware(person.updated_at))]
    state = [person_state(actor), person_state(target), [
        [school.school_id, school.status, str(aware(school.updated_at))]
        for school in sorted(schools.values(), key=lambda school: school.school_id)
    ]]
    return hmac.new(settings.SECRET_KEY.encode(), json.dumps(state).encode(), hashlib.sha256).hexdigest()


async def _lock_context(db, actor_id, target_id):
    """Match account-decision lock order: schools, people, then challenges."""
    preliminary = (await db.scalars(select(Staff).where(Staff.id.in_([actor_id, target_id]))
                                    .execution_options(populate_existing=True))).all()
    school_ids = {person.school_id for person in preliminary if person.school_id is not None}
    schools = {school.school_id: school for school in (await db.scalars(
        select(School).where(School.school_id.in_(school_ids)).order_by(School.school_id)
        .with_for_update().execution_options(populate_existing=True)
    )).all()}
    people = {person.id: person for person in (await db.scalars(
        select(Staff).where(Staff.id.in_([actor_id, target_id])).order_by(Staff.id)
        .with_for_update().execution_options(populate_existing=True)
    )).all()}
    actor, target = people.get(actor_id), people.get(target_id)
    if any(person.school_id is not None and person.school_id not in schools
           for person in people.values()):
        raise _expired()
    return actor, target, schools


async def _authorize(db, actor, target, *, school_id=None):
    if actor is None:
        raise _error(403, "email_correction_forbidden", "Administrator access is required.")
    await require_account_access(actor, db)
    if actor.permanent_lock or (actor.locked_until and aware(actor.locked_until) > now_utc()):
        raise _error(403, "email_correction_forbidden", "Unlock your account before correcting an email.")
    if actor.requires_password_change:
        raise _error(403, "email_correction_forbidden", "Change your own password before correcting an email.")
    if school_id is None:
        if actor.role != ROLE_ADMIN or actor.school_id is None:
            raise _error(403, "email_correction_forbidden", "Only the school's principal can correct a teacher's email.")
        if target is None or target.school_id != actor.school_id:
            raise HTTPException(404, detail="Teacher not found in your school.")
        if target.role != ROLE_TEACHER or target.id == actor.id:
            raise _error(403, "email_correction_forbidden", "A principal can correct teacher emails only.")
    else:
        if actor.role != ROLE_SUPER_ADMIN or actor.school_id is not None:
            raise _error(403, "email_correction_forbidden", "Only the system operator can correct a principal's email.")
        if target is None or target.school_id != school_id or target.role != ROLE_ADMIN:
            raise HTTPException(404, detail="Principal not found for this school.")
        principals = (await db.scalars(select(Staff.id).where(
            Staff.school_id == school_id, Staff.role == ROLE_ADMIN,
        ))).all()
        if len(principals) != 1:
            raise _error(409, "principal_ambiguous", "This school has more than one principal account. Resolve the accounts before correcting its email.")


async def _reserve_password_check(db, actor):
    guard = await db.scalar(select(EmailCorrectionGuard).where(
        EmailCorrectionGuard.actor_id == actor.id,
    ).with_for_update().execution_options(populate_existing=True))
    now = now_utc()
    if guard is None:
        # The caller already holds the actor row lock, including for creation.
        guard = EmailCorrectionGuard(actor_id=actor.id, password_window_start=now, password_failures=0)
        db.add(guard)
    if aware(guard.password_window_start) + timedelta(seconds=_PASSWORD_WINDOW) <= now:
        guard.password_window_start, guard.password_failures = now, 0
    if guard.password_failures >= _PASSWORD_LIMIT:
        raise _limited((aware(guard.password_window_start) + timedelta(seconds=_PASSWORD_WINDOW) - now).total_seconds())
    guard.password_failures += 1
    window = aware(guard.password_window_start)
    await db.commit()
    return window


async def _finish_password_check(db, actor_id, window, verified, target_id, request):
    # Audit rows carry staff foreign keys. Lock the people before the guard,
    # matching reservation, rather than acquiring an FK lock after the guard.
    actor, target, _ = await _lock_context(db, actor_id, target_id)
    guard = await db.scalar(select(EmailCorrectionGuard).where(
        EmailCorrectionGuard.actor_id == actor_id,
    ).with_for_update().execution_options(populate_existing=True))
    if verified and guard and aware(guard.password_window_start) == window:
        guard.password_failures = max(0, guard.password_failures - 1)
    if not verified:
        await log_event(db, "email_correction_password_failed", actor_id=actor.id if actor else None,
                        target_user_id=target.id if target else None, request=request)
    await db.commit()


async def _check_available(db, email, target_id):
    await lock_email_identity(db, email)
    existing = await db.scalar(select(Staff.id).where(
        func.lower(Staff.email) == email, Staff.id != target_id,
    ).limit(1))
    if existing is not None:
        raise _error(409, "email_already_used", "That email address is already used by another account.")


async def _check_send_limits(db, actor_id, target_id):
    now = now_utc()
    recent = (await db.scalars(select(EmailCorrectionChallenge).where(
        or_(EmailCorrectionChallenge.actor_id == actor_id, EmailCorrectionChallenge.staff_id == target_id),
        EmailCorrectionChallenge.created_at > now - timedelta(hours=1),
    ))).all()
    target_requests = [item for item in recent if item.staff_id == target_id]
    actor_requests = [item for item in recent if item.actor_id == actor_id]
    if target_requests:
        latest = max(aware(item.created_at) for item in target_requests)
        if latest + timedelta(seconds=_SEND_COOLDOWN) > now:
            raise _limited((latest + timedelta(seconds=_SEND_COOLDOWN) - now).total_seconds())
    for items, maximum in ((target_requests, _TARGET_HOURLY_LIMIT), (actor_requests, _ACTOR_HOURLY_LIMIT)):
        if len(items) >= maximum:
            raise _limited((min(aware(item.created_at) for item in items) + timedelta(hours=1) - now).total_seconds())


async def request_email_correction(db, actor_id, target_id, payload, request, *, school_id=None):
    actor, target, schools = await _lock_context(db, actor_id, target_id)
    await _authorize(db, actor, target, school_id=school_id)
    email = canonical_email(payload.new_email)
    if email == canonical_email(target.email):
        raise _error(409, "email_unchanged", "Enter a different email address that the account owner can access.")
    state = _state_digest(actor, target, schools)
    password_hash = actor.password_hash
    window = await _reserve_password_check(db, actor)
    # No open transaction or pool connection while bcrypt executes.
    verified = await async_verify_password(payload.current_password, password_hash)
    await _finish_password_check(db, actor_id, window, verified, target_id, request)
    if not verified:
        raise _error(403, "password_incorrect", "Your current password is incorrect.")

    # Canonical identity locks precede every school/person mutation lock, as
    # they do in registration. This avoids cross-flow uniqueness deadlocks.
    await lock_email_identity(db, email)
    actor, target, schools = await _lock_context(db, actor_id, target_id)
    await _authorize(db, actor, target, school_id=school_id)
    if not hmac.compare_digest(state, _state_digest(actor, target, schools)):
        raise _expired()
    await _check_available(db, email, target.id)
    await _check_send_limits(db, actor.id, target.id)
    now, challenge_id, code = now_utc(), uuid4(), generate_otp()
    await db.execute(update(EmailCorrectionChallenge).where(
        EmailCorrectionChallenge.staff_id == target.id, EmailCorrectionChallenge.used_at.is_(None),
    ).values(used_at=now).execution_options(synchronize_session=False))
    db.add(EmailCorrectionChallenge(
        id=challenge_id, staff_id=target.id, actor_id=actor.id, new_email=email,
        code_digest=code_digest(challenge_id, code), state_digest=state,
        reason=payload.reason, expires_at=now + timedelta(minutes=settings.OTP_EXPIRY_MINUTES),
    ))
    await log_event(db, "email_correction_requested", actor_id=actor.id, target_user_id=target.id,
                    detail=f"Identity confirmed by administrator. Reason: {payload.reason}", request=request)
    name, requested_by = target.full_name, actor.full_name
    await db.commit()
    try:
        await send_email_correction_code(email, code, challenge_id=str(challenge_id),
                                         full_name=name, requested_by=requested_by)
    except EmailDeliveryError:
        failed_actor, failed_target, _ = await _lock_context(db, actor_id, target_id)
        await db.execute(update(EmailCorrectionChallenge).where(
            EmailCorrectionChallenge.id == challenge_id,
        ).values(used_at=now_utc()).execution_options(synchronize_session=False))
        await log_event(db, "email_correction_delivery_failed", actor_id=failed_actor.id if failed_actor else None,
                        target_user_id=failed_target.id if failed_target else None, request=request)
        await db.commit()
        raise _error(503, "email_delivery_failed", "The verification email could not be sent. The account email has not changed. Please try again later.") from None
    actor, target, schools = await _lock_context(db, actor_id, target_id)
    if actor is None or target is None or not hmac.compare_digest(state, _state_digest(actor, target, schools)):
        await db.execute(update(EmailCorrectionChallenge).where(
            EmailCorrectionChallenge.id == challenge_id,
        ).values(used_at=now_utc()).execution_options(synchronize_session=False))
        await db.commit()
        raise _expired()
    delivered = await db.execute(update(EmailCorrectionChallenge).where(
        EmailCorrectionChallenge.id == challenge_id, EmailCorrectionChallenge.used_at.is_(None),
    ).values(delivered_at=now_utc()).execution_options(synchronize_session=False))
    await db.commit()
    if delivered.rowcount != 1:
        raise _expired()
    return {"challenge_id": str(challenge_id), "expires_in": settings.OTP_EXPIRY_MINUTES * 60,
            "new_email": email, "message": "Verification sent to the new email. The account owner must open that email and verify the code to complete the correction."}


async def verify_email_correction(db, payload, request):
    initial = await db.get(EmailCorrectionChallenge, payload.challenge_id)
    if initial is None:
        raise _expired()
    await lock_email_identity(db, initial.new_email)
    actor, target, schools = await _lock_context(db, initial.actor_id, initial.staff_id)
    challenge = await db.scalar(select(EmailCorrectionChallenge).where(
        EmailCorrectionChallenge.id == payload.challenge_id,
    ).with_for_update().execution_options(populate_existing=True))
    now = now_utc()
    if (challenge is None or actor is None or target is None or challenge.used_at is not None
            or challenge.attempts >= settings.OTP_MAX_ATTEMPTS or aware(challenge.expires_at) <= now
            or not hmac.compare_digest(challenge.state_digest, _state_digest(actor, target, schools))):
        raise _expired()
    try:
        await _authorize(db, actor, target, school_id=target.school_id if target.role == ROLE_ADMIN else None)
    except HTTPException:
        raise _expired() from None
    if challenge.delivered_at is None:
        raise _error(409, "email_correction_pending", "The verification email is still being sent. Please try again shortly.")
    eligible = (
        EmailCorrectionChallenge.id == challenge.id, EmailCorrectionChallenge.used_at.is_(None),
        EmailCorrectionChallenge.attempts < settings.OTP_MAX_ATTEMPTS,
        EmailCorrectionChallenge.expires_at > now,
    )
    if (canonical_email(payload.new_email) != challenge.new_email
            or not hmac.compare_digest(challenge.code_digest, code_digest(challenge.id, payload.code))):
        await db.execute(update(EmailCorrectionChallenge).where(*eligible).values(
            attempts=EmailCorrectionChallenge.attempts + 1,
        ).execution_options(synchronize_session=False))
        exhausted = challenge.attempts + 1 >= settings.OTP_MAX_ATTEMPTS
        await db.commit()
        if exhausted:
            raise _expired()
        raise _error(400, "email_correction_invalid", "Invalid verification code. Check the code and try again.")
    await _check_available(db, challenge.new_email, target.id)
    consumed = await db.execute(update(EmailCorrectionChallenge).where(*eligible).values(
        used_at=now, completed_at=now,
    ).execution_options(synchronize_session=False))
    if consumed.rowcount != 1:
        await db.rollback()
        raise _expired()
    previous_email, email, name, target_id, actor_id = target.email, challenge.new_email, target.full_name, target.id, actor.id
    target.email = email
    await db.execute(update(StaffSession).where(StaffSession.staff_id == target_id).values(is_active=False))
    await db.execute(update(StaffDevice).where(StaffDevice.staff_id == target_id).values(is_trusted=False, last_2fa_verified=None))
    await db.execute(update(OTPCode).where(OTPCode.staff_id == target_id).values(is_used=True))
    await db.execute(update(PasswordReset).where(PasswordReset.staff_id == target_id).values(
        used_at=now, reset_token=None, token_expires=None,
    ))
    await db.execute(update(ResubmissionChallenge).where(ResubmissionChallenge.staff_id == target_id).values(
        used_at=now, token_digest=None, token_expires_at=None,
    ))
    await db.execute(update(EmailCorrectionChallenge).where(
        or_(EmailCorrectionChallenge.staff_id == target_id, EmailCorrectionChallenge.actor_id == target_id),
        EmailCorrectionChallenge.used_at.is_(None),
    ).values(used_at=now).execution_options(synchronize_session=False))
    try:
        await log_event(db, "email_correction_completed", actor_id=actor_id, target_user_id=target_id,
                        detail="Replacement email verified by its owner. Account records and approval status retained; existing sign-in credentials revoked.", request=request)
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise _error(409, "email_already_used", "That email address is already used by another account.") from None
    # Completion is durable before notifications. The old inbox may not exist.
    results = await asyncio.gather(
        send_email_correction_notice(previous_email, full_name=name, new_email=email, is_previous=True),
        send_email_correction_notice(email, full_name=name, new_email=email, is_previous=False),
        return_exceptions=True,
    )
    if any(isinstance(result, BaseException) for result in results):
        log.warning("An email correction notification could not be delivered.")
    return {"message": "Your email has been corrected. Sign in using your new email address. Your account records and approval status have been preserved.", "email": email, "user_id": str(target_id)}

