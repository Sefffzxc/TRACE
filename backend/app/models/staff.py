"""Staff, devices, sessions, OTP codes, password resets and audit trail."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.session import Base
from app.db.types import GUID, IPAddress

# Account roles
ROLE_ADMIN = "admin"
ROLE_TEACHER = "teacher"

# Account statuses
STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_DEACTIVATED = "deactivated"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Staff(Base):
    """A member of school staff — an administrator or a teacher."""

    __tablename__ = "staff"

    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    email = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)

    first_name = Column(String(100), nullable=False)
    middle_name = Column(String(100), nullable=True)
    last_name = Column(String(100), nullable=False)
    contact_number = Column(String(20), nullable=False)

    role = Column(String(20), nullable=False, default=ROLE_TEACHER)
    employee_no = Column(String(30), nullable=True, unique=True)
    department = Column(String(100), nullable=True)
    profile_pic = Column(Text, nullable=True)

    account_status = Column(String(20), nullable=False, default=STATUS_PENDING)
    rejection_reason = Column(Text, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    is_approved = Column(Boolean, nullable=False, default=False)
    requires_password_change = Column(Boolean, nullable=False, default=False)

    # Brute-force protection
    login_attempts = Column(Integer, nullable=False, default=0)
    locked_until = Column(DateTime(timezone=True), nullable=True)
    lockout_count = Column(Integer, nullable=False, default=0)
    permanent_lock = Column(Boolean, nullable=False, default=False)

    last_login_at = Column(DateTime(timezone=True), nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    approved_by = Column(GUID(), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    devices = relationship(
        "StaffDevice", back_populates="staff", cascade="all, delete-orphan"
    )
    sessions = relationship(
        "StaffSession", back_populates="staff", cascade="all, delete-orphan"
    )
    otp_codes = relationship(
        "OTPCode", back_populates="staff", cascade="all, delete-orphan"
    )
    password_resets = relationship(
        "PasswordReset", back_populates="staff", cascade="all, delete-orphan"
    )

    @property
    def full_name(self) -> str:
        middle = f" {self.middle_name}" if self.middle_name else ""
        return f"{self.first_name}{middle} {self.last_name}"

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<Staff {self.email} ({self.role})>"


class StaffDevice(Base):
    """A browser/device a staff member has signed in from.

    A trusted device skips the 2FA challenge until the interval lapses.
    """

    __tablename__ = "staff_devices"

    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    staff_id = Column(
        GUID(), ForeignKey("staff.id", ondelete="CASCADE"), nullable=False, index=True
    )
    device_id = Column(String(255), nullable=False, index=True)
    device_name = Column(String(255), nullable=True)
    ip_address = Column(IPAddress(), nullable=True)
    user_agent = Column(Text, nullable=True)
    is_trusted = Column(Boolean, nullable=False, default=True)
    last_2fa_verified = Column(DateTime(timezone=True), nullable=True)
    last_used = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    staff = relationship("Staff", back_populates="devices")
    sessions = relationship(
        "StaffSession", back_populates="device", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_staff_devices_staff_device", "staff_id", "device_id"),
    )


class StaffSession(Base):
    """An issued access/refresh token pair."""

    __tablename__ = "sessions"

    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    staff_id = Column(
        GUID(), ForeignKey("staff.id", ondelete="CASCADE"), nullable=False, index=True
    )
    device_id = Column(
        GUID(),
        ForeignKey("staff_devices.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    access_token = Column(Text, nullable=False)
    refresh_token = Column(Text, nullable=False, index=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    last_activity = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    staff = relationship("Staff", back_populates="sessions")
    device = relationship("StaffDevice", back_populates="sessions")


class OTPCode(Base):
    """A one-time passcode for login 2FA or password reset."""

    __tablename__ = "otp_codes"

    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    staff_id = Column(
        GUID(), ForeignKey("staff.id", ondelete="CASCADE"), nullable=False, index=True
    )
    code = Column(String(6), nullable=False)
    purpose = Column(String(20), nullable=False)  # login | password-reset
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)
    is_used = Column(Boolean, nullable=False, default=False)
    attempts = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    staff = relationship("Staff", back_populates="otp_codes")


class PasswordReset(Base):
    """Throttling window plus the short-lived reset token."""

    __tablename__ = "password_resets"

    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    staff_id = Column(
        GUID(), ForeignKey("staff.id", ondelete="CASCADE"), nullable=False, index=True
    )
    request_count = Column(Integer, nullable=False, default=1)
    window_start = Column(DateTime(timezone=True), default=_utcnow)
    locked_until = Column(DateTime(timezone=True), nullable=True)
    reset_token = Column(String(255), unique=True, nullable=True, index=True)
    token_expires = Column(DateTime(timezone=True), nullable=True)
    is_admin_reset = Column(Boolean, nullable=False, default=False)
    used_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    staff = relationship("Staff", back_populates="password_resets")


class AuditLog(Base):
    """Every privileged action, kept for accountability.

    ``action`` values include: register, login_success, login_failed,
    account_locked, account_perm_lock, approve_user, reject_user,
    deactivate_user, reactivate_user, force_reset_password, password_changed.
    """

    __tablename__ = "audit_logs"

    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    admin_id = Column(GUID(), ForeignKey("staff.id", ondelete="SET NULL"), nullable=True)
    target_user_id = Column(
        GUID(), ForeignKey("staff.id", ondelete="SET NULL"), nullable=True
    )
    action = Column(String(50), nullable=False, index=True)
    detail = Column(Text, nullable=True)
    ip_address = Column(IPAddress(), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)

    admin = relationship("Staff", foreign_keys=[admin_id])
    target_user = relationship("Staff", foreign_keys=[target_user_id])
