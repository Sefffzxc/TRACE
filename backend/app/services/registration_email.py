"""One-use registration proofs, code digests and database-backed send limits."""
from __future__ import annotations

import hashlib
import hmac
import math
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import case, select, update
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.models.registration_email import RegistrationEmailChallenge, RegistrationEmailLimit
from app.services.email_identity import canonical_email

CODE_SECONDS = 300
TOKEN_SECONDS = 900


def now_utc():
    return datetime.now(timezone.utc)


def aware(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def code_digest(challenge_id, code):
    return hmac.new(settings.SECRET_KEY.encode(),
                    f"registration-email:{challenge_id}:{code}".encode(), hashlib.sha256).hexdigest()


def token_digest(token):
    return hashlib.sha256(("registration-email-token:" + token).encode()).hexdigest()


def proof_required():
    return HTTPException(400, detail={
        "code": "email_verification_required",
        "message": "Verify your email address before submitting your registration. Verification may have expired; request a new code if needed.",
    })


def invalid_code():
    return HTTPException(400, detail={
        "code": "registration_code_invalid",
        "message": "Invalid or expired verification code. Check the code or request a new one.",
    })


async def check_proof(db, token, *, email, kind):
    if not token:
        raise proof_required()
    challenge = await db.scalar(select(RegistrationEmailChallenge).where(
        RegistrationEmailChallenge.token_digest == token_digest(token),
        RegistrationEmailChallenge.email == canonical_email(email),
        RegistrationEmailChallenge.kind == kind,
        RegistrationEmailChallenge.verified_at.is_not(None),
        RegistrationEmailChallenge.used_at.is_(None),
        RegistrationEmailChallenge.token_expires_at > now_utc(),
    ))
    if challenge is None:
        raise proof_required()
    return challenge


async def consume_proof(db, token, *, email, kind):
    """Claim in the account-creation transaction; rollback restores the proof."""
    if not token:
        raise proof_required()
    claimed = await db.execute(update(RegistrationEmailChallenge).where(
        RegistrationEmailChallenge.token_digest == token_digest(token),
        RegistrationEmailChallenge.email == canonical_email(email),
        RegistrationEmailChallenge.kind == kind,
        RegistrationEmailChallenge.verified_at.is_not(None),
        RegistrationEmailChallenge.used_at.is_(None),
        RegistrationEmailChallenge.token_expires_at > now_utc(),
    ).values(used_at=now_utc()).execution_options(synchronize_session=False))
    if claimed.rowcount != 1:
        raise proof_required()


async def _reserve_bucket(db, key, maximum, seconds):
    now = now_utc()
    deadline = now + timedelta(seconds=seconds)
    # An UPDATE is the first statement so SQLite also serializes competing
    # writers without upgrading a read transaction. PostgreSQL updates lock
    # the matching row; INSERT's savepoint handles a concurrently new bucket.
    for _ in range(2):
        claimed = await db.execute(update(RegistrationEmailLimit).where(
            RegistrationEmailLimit.key == key,
            (RegistrationEmailLimit.resets_at <= now) | (RegistrationEmailLimit.count < maximum),
        ).values(
            count=case((RegistrationEmailLimit.resets_at <= now, 1),
                       else_=RegistrationEmailLimit.count + 1),
            resets_at=case((RegistrationEmailLimit.resets_at <= now, deadline),
                           else_=RegistrationEmailLimit.resets_at),
        ).execution_options(synchronize_session=False))
        if claimed.rowcount == 1:
            return
        bucket = await db.get(RegistrationEmailLimit, key, populate_existing=True)
        if bucket is not None:
            retry = max(1, math.ceil((aware(bucket.resets_at) - now).total_seconds()))
            raise HTTPException(429, detail={
                "code": "registration_email_throttled",
                "message": f"Please wait {retry} seconds before requesting another verification email.",
                "retry_after": retry,
            }, headers={"Retry-After": str(retry)})
        try:
            async with db.begin_nested():
                db.add(RegistrationEmailLimit(key=key, count=1, resets_at=deadline))
                await db.flush()
            return
        except IntegrityError:
            pass
    raise HTTPException(429, detail={"code": "registration_email_throttled",
                                    "message": "Please wait before requesting another verification email.",
                                    "retry_after": 60}, headers={"Retry-After": "60"})


async def reserve_send(db, *, email, ip):
    recipient = hashlib.sha256(canonical_email(email).encode()).hexdigest()
    address = hashlib.sha256(ip.encode()).hexdigest()
    # Allow a testing room on shared school Wi-Fi, while one recipient can
    # receive only one code each minute and five codes each hour.
    await _reserve_bucket(db, "recipient-minute:" + recipient, 1, 60)
    await _reserve_bucket(db, "recipient-hour:" + recipient, 5, 3600)
    await _reserve_bucket(db, "network:" + address, settings.AUTH_REGISTER_IP_LIMIT, 300)
