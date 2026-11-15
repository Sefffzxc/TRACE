"""Application decisions and narrowly scoped email-verification challenges.

These are additive tables. Staff ids and existing school records are preserved.
The correction token is stored only as a digest and is never a login session.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text

from app.db.session import Base
from app.db.types import GUID


def _now():
    return datetime.now(timezone.utc)


class ApplicationEvent(Base):
    __tablename__ = "application_events"

    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    staff_id = Column(GUID(), ForeignKey("staff.id", ondelete="SET NULL"), nullable=True, index=True)
    school_id = Column(Integer, ForeignKey("schools.school_id", ondelete="SET NULL"), nullable=True, index=True)
    actor_id = Column(GUID(), ForeignKey("staff.id", ondelete="SET NULL"), nullable=True)
    action = Column(String(50), nullable=False)
    from_status = Column(String(20), nullable=True)
    to_status = Column(String(20), nullable=True)
    reason = Column(Text, nullable=True)
    detail = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now, index=True)


class ResubmissionChallenge(Base):
    __tablename__ = "resubmission_challenges"

    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    staff_id = Column(GUID(), ForeignKey("staff.id", ondelete="CASCADE"), nullable=False, index=True)
    code_digest = Column(String(64), nullable=False)
    state_digest = Column(String(64), nullable=False)
    attempts = Column(Integer, nullable=False, default=0)
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)
    verified_at = Column(DateTime(timezone=True), nullable=True)
    token_digest = Column(String(64), nullable=True, unique=True, index=True)
    token_expires_at = Column(DateTime(timezone=True), nullable=True)
    used_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
