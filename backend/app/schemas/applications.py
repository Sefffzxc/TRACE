"""Email ownership proof authorizes edits, never normal account access."""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.schemas.schools import SchoolProfile


class ResubmissionRequest(BaseModel):
    email: EmailStr
    captcha_token: Optional[str] = Field(default=None, max_length=2048, repr=False)


class ResubmissionVerify(BaseModel):
    email: EmailStr
    challenge_id: UUID
    code: str = Field(pattern=r"^\d{6}$", repr=False)


class ResubmissionSubmit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=20, max_length=255, repr=False)
    first_name: str = Field(min_length=1, max_length=100)
    middle_name: Optional[str] = Field(default=None, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    contact_number: str = Field(min_length=7, max_length=20)
    employee_no: Optional[str] = Field(default=None, max_length=30)
    department: Optional[str] = Field(default=None, max_length=100)
    school_id: Optional[int] = Field(default=None, gt=0)
    confirm_school_change: bool = False
    school: Optional[SchoolProfile] = None

    @field_validator("first_name", "last_name", "contact_number", mode="before")
    @classmethod
    def trim_required(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("middle_name", "employee_no", "department", mode="before")
    @classmethod
    def trim_optional(cls, value):
        return value.strip() or None if isinstance(value, str) else value
