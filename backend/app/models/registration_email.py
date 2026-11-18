"""Short-lived email ownership proofs created before any account exists."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Integer, String

from app.db.session import Base
from app.db.types import GUID


def _now():
    return datetime.now(timezone.utc)


class RegistrationEmailChallenge(Base):
    __tablename__ = "registration_email_challenges"

    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    email = Column(String(255), nullable=False, index=True)
    kind = Column(String(12), nullable=False)
    code_digest = Column(String(64), nullable=False)
    attempts = Column(Integer, nullable=False, default=0)
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)
    verified_at = Column(DateTime(timezone=True), nullable=True)
    token_digest = Column(String(64), nullable=True, unique=True, index=True)
    token_expires_at = Column(DateTime(timezone=True), nullable=True)
    used_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)


class RegistrationEmailLimit(Base):
    """Shared send quotas survive process restarts and multiple API workers."""

    __tablename__ = "registration_email_limits"

    key = Column(String(90), primary_key=True)
    count = Column(Integer, nullable=False, default=0)
    resets_at = Column(DateTime(timezone=True), nullable=False, index=True)
