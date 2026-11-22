"""Correct a rejected application after proving ownership of its email.

Email codes and correction tokens have their own purpose, expiry, attempt limits
and single-use records. They never issue a login or change an email/role.
"""
from __future__ import annotations

import hmac
import secrets
from datetime import timedelta
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import limit_resubmission_request, limit_resubmission_submit, limit_resubmission_verify
from app.core.config import settings
from app.core.security import generate_otp
from app.db.session import get_db
from app.models.applications import ApplicationEvent, ResubmissionChallenge
from app.models.schools import SCHOOL_APPROVED, SCHOOL_PENDING, School
from app.models.staff import OTPCode, ROLE_ADMIN, ROLE_TEACHER, STATUS_PENDING, STATUS_REJECTED, Staff, StaffDevice, StaffSession
from app.schemas.applications import ResubmissionRequest, ResubmissionSubmit, ResubmissionVerify
from app.schemas.schools import SchoolProfile
from app.schemas.validation import format_school_name
from app.services.contact_numbers import ensure_contact_available
from app.services.applications import (
    application_eligible, application_state_digest, aware, can_change_application_school,
    correction_token_digest, ensure_legacy_rejection_event, now_utc,
    record_application_event, verification_digest,
)
from app.services.audit import log_event
from app.services.captcha import verify_captcha
from app.services.email import EmailDeliveryError, send_otp_email


router = APIRouter(prefix="/api/auth/resubmission", tags=["Application corrections"])
_TOKEN_SECONDS = 15 * 60
_GENERIC = "If this email belongs to an eligible rejected application, a verification code has been sent."


def _invalid_code():
    return HTTPException(400, detail={
        "code": "resubmission_code_invalid",
        "message": "Invalid or expired verification code. Request a new code if needed.",
    })


def _invalid_token():
    return HTTPException(401, detail={
        "code": "resubmission_expired",
        "message": "Application verification expired or is no longer valid. Verify your email again.",
    })


async def _school(db, person):
    return await db.get(School, person.school_id) if person is not None and person.school_id is not None else None


async def _lock_application(db, staff_id, destination_school_id=None):
    """Serialize corrections with operator decisions: schools, then applicant."""
    person = await db.get(Staff, staff_id)
    if person is None:
        return None, None
    ids = {value for value in (person.school_id, destination_school_id) if value is not None}
    schools = (await db.scalars(select(School).where(School.school_id.in_(ids))
               .order_by(School.school_id).with_for_update()
               .execution_options(populate_existing=True))).all()
    person = await db.scalar(select(Staff).where(Staff.id == staff_id).with_for_update()
                             .execution_options(populate_existing=True))
    school = next((value for value in schools if person is not None and value.school_id == person.school_id), None)
    return person, school


async def _application_view(db, person, school):
    history = (await db.scalars(select(ApplicationEvent).where(
        ApplicationEvent.staff_id == person.id,
    ).order_by(ApplicationEvent.created_at, ApplicationEvent.id))).all()
    return {
        "id": str(person.id), "email": person.email, "role": person.role,
        "first_name": person.first_name, "middle_name": person.middle_name,
        "last_name": person.last_name, "contact_number": person.contact_number,
        "employee_no": person.employee_no, "department": person.department,
        "rejection_reason": person.rejection_reason,
        "school_id": person.school_id, "school_name": format_school_name(school.name) if school else None,
        # Legacy rows can contain incomplete details. Let the verified owner
        # open the form and correct them; validate new details on submission.
        "school": {field: getattr(school, field) for field in SchoolProfile.model_fields}
                  if person.role == ROLE_ADMIN and school else None,
        "can_change_school": await can_change_application_school(db, person),
        "history": [{
            "id": str(event.id), "action": event.action,
            "from_status": event.from_status, "to_status": event.to_status,
            "reason": event.reason, "detail": event.detail, "created_at": event.created_at,
        } for event in history],
    }


@router.post("/request", status_code=202, dependencies=[Depends(limit_resubmission_request)])
async def request_correction(payload: ResubmissionRequest, db: AsyncSession = Depends(get_db)):
    await verify_captcha(payload.captcha_token, action="resubmit")
    challenge_id = uuid4()
    response = {"message": _GENERIC, "challenge_id": str(challenge_id)}
    person = await db.scalar(select(Staff).where(Staff.email == str(payload.email).lower()))
    school = await _school(db, person)
    if not application_eligible(person, school):
        return response
    state = application_state_digest(person, school)
    code = generate_otp()
    # A room of applicants must not hold database connections while the email
    # provider is processing their verification messages.
    await db.commit()
    try:
        await send_otp_email(person.email, code, purpose="resubmission")
    except EmailDeliveryError:
        # Keep unknown, blocked, approved and undeliverable addresses private.
        return response
    person, school = await _lock_application(db, person.id)
    if not application_eligible(person, school) or application_state_digest(person, school) != state:
        return response
    await ensure_legacy_rejection_event(db, person)
    await db.execute(update(ResubmissionChallenge).where(
        ResubmissionChallenge.staff_id == person.id,
        ResubmissionChallenge.verified_at.is_(None), ResubmissionChallenge.used_at.is_(None),
    ).values(used_at=now_utc()).execution_options(synchronize_session=False))
    db.add(ResubmissionChallenge(
        id=challenge_id, staff_id=person.id, state_digest=state,
        code_digest=verification_digest(challenge_id, code),
        expires_at=now_utc() + timedelta(minutes=settings.OTP_EXPIRY_MINUTES),
    ))
    await db.commit()
    return response


@router.post("/verify", dependencies=[Depends(limit_resubmission_verify)])
async def verify_correction(payload: ResubmissionVerify, db: AsyncSession = Depends(get_db)):
    challenge = await db.get(ResubmissionChallenge, payload.challenge_id)
    person = await db.get(Staff, challenge.staff_id) if challenge else None
    school = await _school(db, person)
    now = now_utc()
    if (
        challenge is None or person is None or person.email != str(payload.email).lower()
        or not application_eligible(person, school)
        or challenge.used_at is not None or challenge.verified_at is not None
        or challenge.attempts >= settings.OTP_MAX_ATTEMPTS or aware(challenge.expires_at) <= now
        or application_state_digest(person, school) != challenge.state_digest
    ):
        raise _invalid_code()
    eligible = (
        ResubmissionChallenge.id == challenge.id,
        ResubmissionChallenge.used_at.is_(None), ResubmissionChallenge.verified_at.is_(None),
        ResubmissionChallenge.attempts < settings.OTP_MAX_ATTEMPTS,
        ResubmissionChallenge.expires_at > now,
    )
    if not hmac.compare_digest(challenge.code_digest, verification_digest(challenge.id, payload.code)):
        await db.execute(update(ResubmissionChallenge).where(*eligible).values(
            attempts=ResubmissionChallenge.attempts + 1,
        ).execution_options(synchronize_session=False))
        await db.commit()
        raise _invalid_code()
    token = secrets.token_urlsafe(32)
    claimed = await db.execute(update(ResubmissionChallenge).where(*eligible).values(
        verified_at=now, token_digest=correction_token_digest(token),
        token_expires_at=now + timedelta(seconds=_TOKEN_SECONDS),
    ).execution_options(synchronize_session=False))
    if claimed.rowcount != 1:
        await db.rollback()
        raise _invalid_code()
    application = await _application_view(db, person, school)
    await db.commit()
    return {"resubmission_token": token, "expires_in": _TOKEN_SECONDS, "application": application}


@router.post("/submit", dependencies=[Depends(limit_resubmission_submit)])
async def submit_correction(
    payload: ResubmissionSubmit, request: Request, db: AsyncSession = Depends(get_db),
):
    digest = correction_token_digest(payload.token)
    challenge = await db.scalar(select(ResubmissionChallenge).where(ResubmissionChallenge.token_digest == digest))
    if challenge is None:
        raise _invalid_token()
    # Lock both school choices before the applicant, preventing a destination
    # being blocked while a verified change of school is committed.
    person, school = await _lock_application(db, challenge.staff_id, payload.school_id)
    now = now_utc()
    if (
        not application_eligible(person, school) or challenge.used_at is not None
        or challenge.verified_at is None or challenge.token_expires_at is None
        or aware(challenge.token_expires_at) <= now
        or application_state_digest(person, school) != challenge.state_digest
    ):
        raise _invalid_token()

    await ensure_contact_available(db, payload.contact_number, exclude_staff_id=person.id)
    destination = school
    changing_school = payload.school_id is not None and payload.school_id != person.school_id
    if person.role == ROLE_TEACHER:
        if payload.school is not None:
            raise HTTPException(422, detail="Teachers cannot change a school profile.")
        if changing_school:
            if not payload.confirm_school_change:
                raise HTTPException(422, detail="Confirm the change of school before resubmitting.")
            if not await can_change_application_school(db, person):
                raise HTTPException(409, detail="This account has prior approval or school records. Contact your administrator for a school transfer.")
            destination = await db.get(School, payload.school_id)
        if destination is None or destination.status != SCHOOL_APPROVED:
            raise HTTPException(422, detail="Choose an approved school before resubmitting.")
    elif payload.school_id not in (None, person.school_id) or payload.confirm_school_change:
        raise HTTPException(422, detail="A principal must correct the existing school application.")

    employee_no = (
        payload.employee_no if "employee_no" in payload.model_fields_set else person.employee_no
    ) or None
    if employee_no and await db.scalar(select(Staff.id).where(
        Staff.school_id == destination.school_id, Staff.employee_no == employee_no, Staff.id != person.id,
    ).limit(1)) is not None:
        raise HTTPException(409, detail="That employee number is already used at this school.")
    if person.role == ROLE_ADMIN and payload.school is not None:
        duplicate = await db.scalar(select(School.school_id).where(
            School.school_id != school.school_id, School.status.in_([SCHOOL_PENDING, SCHOOL_APPROVED]),
            func.lower(School.name) == payload.school.name.lower(),
            func.lower(School.city) == payload.school.city.lower(),
        ).limit(1))
        if duplicate is not None:
            raise HTTPException(409, detail="A school with that name and city is already registered or awaiting approval.")

    consumed = await db.execute(update(ResubmissionChallenge).where(
        ResubmissionChallenge.id == challenge.id, ResubmissionChallenge.used_at.is_(None),
        ResubmissionChallenge.token_digest == digest, ResubmissionChallenge.token_expires_at > now,
    ).values(used_at=now).execution_options(synchronize_session=False))
    if consumed.rowcount != 1:
        await db.rollback()
        raise _invalid_token()
    await ensure_legacy_rejection_event(db, person)
    if changing_school:
        await record_application_event(
            db, person, "school_change", STATUS_REJECTED, STATUS_REJECTED,
            detail=f"Email owner changed an unapproved application from {school.name} to {destination.name}. Existing school records were not transferred.",
        )
        person.school_id = destination.school_id
    for field in ("first_name", "middle_name", "last_name", "contact_number"):
        setattr(person, field, getattr(payload, field))
    for field in ("employee_no", "department"):
        if field in payload.model_fields_set:
            setattr(person, field, getattr(payload, field))
    person.account_status = STATUS_PENDING
    person.is_approved = False
    person.rejection_reason = None
    if person.role == ROLE_ADMIN:
        if payload.school is not None:
            for field, value in payload.school.model_dump().items():
                setattr(school, field, value)
        school.status = SCHOOL_PENDING
        school.rejection_reason = None
    await db.execute(update(OTPCode).where(OTPCode.staff_id == person.id).values(is_used=True))
    await db.execute(update(StaffDevice).where(StaffDevice.staff_id == person.id).values(is_trusted=False))
    await db.execute(update(StaffSession).where(StaffSession.staff_id == person.id).values(is_active=False))
    try:
        await record_application_event(db, person, "resubmitted", STATUS_REJECTED, STATUS_PENDING,
                                       detail="Applicant verified their email and corrected this application.")
        await log_event(db, "application_resubmitted", target_user_id=person.id,
                        detail=f"Application returned to Pending at school #{person.school_id}.", request=request)
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, detail="These application details conflict with an existing registration. Check the employee number and try again.") from None
    return {"message": "Your corrected application has been submitted and is awaiting approval again.",
            "account_status": STATUS_PENDING}
