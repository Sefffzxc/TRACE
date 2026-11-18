"""One-use email recovery challenges and durable administrator throttles."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text

from app.db.session import Base
from app.db.types import GUID


def _now():
    return datetime.now(timezone.utc)


class EmailCorrectionChallenge(Base):
    __tablename__ = "email_correction_challenges"

    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    staff_id = Column(GUID(), ForeignKey("staff.id", ondelete="CASCADE"), nullable=False, index=True)
    actor_id = Column(GUID(), ForeignKey("staff.id", ondelete="CASCADE"), nullable=False, index=True)
    new_email = Column(String(255), nullable=False)
    code_digest = Column(String(64), nullable=False)
    state_digest = Column(String(64), nullable=False)
    reason = Column(Text, nullable=False)
    attempts = Column(Integer, nullable=False, default=0)
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)
    delivered_at = Column(DateTime(timezone=True), nullable=True)
    used_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now, index=True)


class EmailCorrectionGuard(Base):
    __tablename__ = "email_correction_guards"

    actor_id = Column(GUID(), ForeignKey("staff.id", ondelete="CASCADE"), primary_key=True)
    password_window_start = Column(DateTime(timezone=True), nullable=False, default=_now)
    # Pending password checks count against the limit until they succeed.
    # This prevents parallel requests bypassing the failed-password ceiling.
    password_failures = Column(Integer, nullable=False, default=0)

