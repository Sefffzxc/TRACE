"""Pydantic models for authentication and staff management."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from app.core.security import validate_password_strength
from app.schemas.validation import MobileNumber


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ── Registration / profile ────────────────────────────────────────────────────
class StaffRegisterRequest(BaseModel):
    """A teacher joining a school that already exists.

    A school's first account — its principal — is created by
    `POST /api/schools/register` instead, together with the school itself.
    This form cannot create one, which is why `role` no longer accepts
    "admin": self-registering as an administrator would have let anyone award
    themselves control of a school they merely picked from a list.
    """

    school_id: int = Field(description="Chosen from GET /api/schools/directory")
    captcha_token: Optional[str] = Field(default=None, max_length=2048, repr=False)
    email_verification_token: Optional[str] = Field(default=None, max_length=200, repr=False)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    middle_name: Optional[str] = Field(default=None, max_length=100)
    contact_number: MobileNumber
    role: Literal["teacher"] = "teacher"
    employee_no: Optional[str] = Field(default=None, max_length=30)
    department: Optional[str] = Field(default=None, max_length=100)

    @field_validator("password")
    @classmethod
    def _strong(cls, v: str) -> str:
        ok, message = validate_password_strength(v)
        if not ok:
            raise ValueError(message)
        return v


class StaffResponse(ORMModel):
    id: uuid.UUID
    email: EmailStr
    first_name: str
    middle_name: Optional[str] = None
    last_name: str
    contact_number: str
    role: str
    employee_no: Optional[str] = None
    department: Optional[str] = None
    profile_pic: Optional[str] = None
    account_status: str
    rejection_reason: Optional[str] = None
    is_active: bool
    is_approved: bool
    requires_password_change: bool
    permanent_lock: bool
    locked_until: Optional[datetime] = None
    last_login_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    approved_at: Optional[datetime] = None


class StaffUpdateProfile(BaseModel):
    first_name: Optional[str] = Field(default=None, max_length=100)
    middle_name: Optional[str] = Field(default=None, max_length=100)
    last_name: Optional[str] = Field(default=None, max_length=100)
    contact_number: Optional[MobileNumber] = None
    department: Optional[str] = Field(default=None, max_length=100)


class StaffAdminUpdate(BaseModel):
    """Admin-only actions on a staff account."""

    action: Literal[
        "approve", "reject", "reopen", "block", "deactivate", "reactivate", "unlock", "force_reset"
    ]
    reason: Optional[str] = Field(default=None, max_length=500)
    role: Optional[Literal["admin", "teacher"]] = None

    @model_validator(mode="after")
    def _decision_reason(self):
        self.reason = self.reason.strip() if self.reason else None
        if self.action in {"reject", "block"} and not self.reason:
            raise ValueError("A reason is required for rejecting or blocking an application.")
        return self


class ApplicationHistoryResponse(ORMModel):
    id: uuid.UUID
    staff_id: Optional[uuid.UUID] = None
    school_id: Optional[int] = None
    actor_id: Optional[uuid.UUID] = None
    actor_name: Optional[str] = None
    action: str
    from_status: Optional[str] = None
    to_status: Optional[str] = None
    reason: Optional[str] = None
    detail: Optional[str] = None
    created_at: Optional[datetime] = None


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=128)

    @field_validator("new_password")
    @classmethod
    def _strong(cls, v: str) -> str:
        ok, message = validate_password_strength(v)
        if not ok:
            raise ValueError(message)
        return v


# ── Login / 2FA ───────────────────────────────────────────────────────────────
class RequiredPasswordChangeRequest(BaseModel):
    new_password: str = Field(min_length=8, max_length=128, repr=False)
    confirm_password: str = Field(min_length=8, max_length=128, repr=False)

    @field_validator("new_password")
    @classmethod
    def _strong(cls, value: str) -> str:
        ok, message = validate_password_strength(value)
        if not ok:
            raise ValueError(message)
        return value

    @model_validator(mode="after")
    def _matching(self):
        if self.new_password != self.confirm_password:
            raise ValueError("The two passwords do not match.")
        return self


class StaffLoginRequest(BaseModel):
    captcha_token: Optional[str] = Field(default=None, max_length=2048, repr=False)
    email: EmailStr
    password: str
    device_id: str = Field(min_length=4, max_length=255)
    device_name: Optional[str] = Field(default=None, max_length=255)


class OTPVerifyRequest(BaseModel):
    user_id: uuid.UUID
    code: str = Field(min_length=6, max_length=6)
    device_id: str
    device_name: Optional[str] = None


class TokenResponse(BaseModel):
    access_token: Optional[str] = None
    refresh_token: Optional[str] = None
    token_type: str = "bearer"
    expires_in: Optional[int] = None
    requires_2fa: bool = False
    requires_password_change: bool = False
    user_id: Optional[str] = None
    user: Optional[StaffResponse] = None
    debug_otp: Optional[str] = None


# ── Password reset ────────────────────────────────────────────────────────────
class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class VerifyResetOTPRequest(BaseModel):
    email: EmailStr
    code: str = Field(min_length=6, max_length=6)


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str = Field(min_length=8, max_length=128)
    confirm_password: str

    @field_validator("new_password")
    @classmethod
    def _strong(cls, v: str) -> str:
        ok, message = validate_password_strength(v)
        if not ok:
            raise ValueError(message)
        return v

    @field_validator("confirm_password")
    @classmethod
    def _match(cls, v: str, info):
        if info.data.get("new_password") and v != info.data["new_password"]:
            raise ValueError("Passwords do not match.")
        return v


# ── Audit ─────────────────────────────────────────────────────────────────────
class AuditLogResponse(ORMModel):
    id: uuid.UUID
    admin_id: Optional[uuid.UUID] = None
    target_user_id: Optional[uuid.UUID] = None
    action: str
    detail: Optional[str] = None
    created_at: Optional[datetime] = None
    admin_name: Optional[str] = None
    target_name: Optional[str] = None
