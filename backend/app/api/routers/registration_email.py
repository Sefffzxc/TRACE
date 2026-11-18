"""Verify ownership of an email before creating a teacher or school request."""
from __future__ import annotations

import hmac
import secrets
from datetime import timedelta
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import limit_otp, limit_register
from app.core.config import settings
from app.core.security import generate_otp
from app.db.session import get_db
from app.models.registration_email import RegistrationEmailChallenge
from app.models.staff import Staff
from app.schemas.registration_email import RegistrationEmailRequest, RegistrationEmailVerify
from app.services.captcha import verify_captcha
from app.services.email import EmailDeliveryError, send_registration_verification_email
from app.services.email_identity import canonical_email
from app.services.registration_email import (
    CODE_SECONDS, TOKEN_SECONDS, aware, code_digest, invalid_code, now_utc,
    reserve_send, token_digest,
)

router = APIRouter(prefix="/api/auth/registration-email", tags=["Registration email"])


@router.post("/request", status_code=202, dependencies=[Depends(limit_register)])
async def request_email_verification(
    payload: RegistrationEmailRequest, request: Request, db: AsyncSession = Depends(get_db),
):
    await verify_captcha(payload.captcha_token, action="register_" + payload.kind)
    email = canonical_email(payload.email)
    ip = request.client.host if request.client else "unknown"
    await reserve_send(db, email=email, ip=ip)
    if await db.scalar(select(Staff.id).where(func.lower(Staff.email) == email).limit(1)) is not None:
        raise HTTPException(409, detail="An account with that email address already exists. Sign in or contact your principal for help.")
    now = now_utc()
    challenge_id, code = uuid4(), generate_otp()
    # A fresh send replaces unused challenges for the same email and purpose.
    await db.execute(update(RegistrationEmailChallenge).where(
        RegistrationEmailChallenge.email == email,
        RegistrationEmailChallenge.kind == payload.kind,
        RegistrationEmailChallenge.used_at.is_(None),
    ).values(used_at=now).execution_options(synchronize_session=False))
    db.add(RegistrationEmailChallenge(
        id=challenge_id, email=email, kind=payload.kind,
        code_digest=code_digest(challenge_id, code),
        expires_at=now + timedelta(seconds=CODE_SECONDS),
    ))
    await db.commit()  # Release the pool connection before the provider call.
    try:
        await send_registration_verification_email(email, code, kind=payload.kind)
    except EmailDeliveryError:
        await db.execute(update(RegistrationEmailChallenge).where(
            RegistrationEmailChallenge.id == challenge_id,
        ).values(used_at=now_utc()).execution_options(synchronize_session=False))
        await db.commit()
        raise HTTPException(503, detail={
            "code": "registration_email_unavailable",
            "message": "We could not send the verification email. Please wait a minute and try again.",
        }) from None
    return {"challenge_id": str(challenge_id), "expires_in": CODE_SECONDS,
            "message": "A verification code has been sent to your email. It expires in five minutes."}


@router.post("/verify", dependencies=[Depends(limit_otp)])
async def verify_registration_email(
    payload: RegistrationEmailVerify, db: AsyncSession = Depends(get_db),
):
    now = now_utc()
    challenge = await db.get(RegistrationEmailChallenge, payload.challenge_id)
    if (challenge is None or challenge.email != canonical_email(payload.email)
            or challenge.used_at is not None or challenge.verified_at is not None
            or challenge.attempts >= settings.OTP_MAX_ATTEMPTS or aware(challenge.expires_at) <= now):
        raise invalid_code()
    eligible = (
        RegistrationEmailChallenge.id == challenge.id,
        RegistrationEmailChallenge.used_at.is_(None),
        RegistrationEmailChallenge.verified_at.is_(None),
        RegistrationEmailChallenge.attempts < settings.OTP_MAX_ATTEMPTS,
        RegistrationEmailChallenge.expires_at > now,
    )
    if not hmac.compare_digest(challenge.code_digest, code_digest(challenge.id, payload.code)):
        await db.execute(update(RegistrationEmailChallenge).where(*eligible).values(
            attempts=RegistrationEmailChallenge.attempts + 1,
        ).execution_options(synchronize_session=False))
        await db.commit()
        raise invalid_code()
    token = secrets.token_urlsafe(32)
    claimed = await db.execute(update(RegistrationEmailChallenge).where(*eligible).values(
        verified_at=now, token_digest=token_digest(token),
        token_expires_at=now + timedelta(seconds=TOKEN_SECONDS),
    ).execution_options(synchronize_session=False))
    if claimed.rowcount != 1:
        await db.rollback()
        raise invalid_code()
    await db.commit()
    return {"email_verification_token": token, "expires_in": TOKEN_SECONDS}
