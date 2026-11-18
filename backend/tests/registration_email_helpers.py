"""Verified-email fixtures for tests of behavior after ownership verification."""
from datetime import timedelta
import secrets
from uuid import uuid4

from app.models.registration_email import RegistrationEmailChallenge
from app.services.email_identity import canonical_email
from app.services.registration_email import code_digest, now_utc, token_digest


async def issue_registration_proof(sessions, email, kind="teacher"):
    """Persist the same verified proof shape produced by the public verifier.

    Request/verification integration lives in test_registration_email. Other
    suites can keep testing approval, CAPTCHA and connection contention using
    an explicit already-verified applicant rather than bypassing the gate.
    """
    now, identity, token = now_utc(), uuid4(), secrets.token_urlsafe(32)
    async with sessions() as db:
        db.add(RegistrationEmailChallenge(
            id=identity, email=canonical_email(email), kind=kind,
            code_digest=code_digest(identity, "654321"),
            expires_at=now + timedelta(minutes=5), verified_at=now,
            token_digest=token_digest(token), token_expires_at=now + timedelta(minutes=15),
        ))
        await db.commit()
    return token
