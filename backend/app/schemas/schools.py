"""Schemas for school registration, approval and settings."""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ── How a school describes itself ─────────────────────────────────────────────
class SchoolProfile(BaseModel):
    """The settings that decide what this school's staff actually see."""

    name: str = Field(min_length=3, max_length=200)
    short_name: Optional[str] = Field(default=None, max_length=40)
    school_type: Literal["public", "private"] = "private"

    address: str = Field(default="", max_length=255)
    city: str = Field(min_length=2, max_length=100)
    province: Optional[str] = Field(default=None, max_length=100)
    region: Optional[str] = Field(default=None, max_length=100)

    contact_number: Optional[str] = Field(default=None, max_length=30)
    contact_email: Optional[EmailStr] = None

    # The question that decides whether the lesson-progress screens appear.
    # Deliberately separate from school_type: most private schools in the
    # Philippines follow the DepEd curriculum and have no booklets either, so
    # public/private is the wrong thing to branch on.
    tracks_units: bool = False
    unit_label_singular: str = Field(default="Module", max_length=40)
    unit_label_plural: str = Field(default="Modules", max_length=40)
    default_units_per_subject: int = Field(default=12, ge=1, le=200)

    passing_mark: float = Field(default=75.0, ge=1, le=100)
    grading_periods: int = Field(default=4, ge=1, le=6)
    grading_period_label: str = Field(default="Quarter", max_length=30)

    @field_validator("name", "city")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()


class SchoolRegisterRequest(BaseModel):
    """One call creates the school and the person who will run it.

    They arrive together on purpose: a school with nobody able to sign in is
    not useful, and a principal with no school has nothing to administer.
    Both are created pending, and the operator's approval releases both.
    """

    school: SchoolProfile

    # The principal's own account.
    first_name: str = Field(min_length=1, max_length=100)
    middle_name: Optional[str] = Field(default=None, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    email: EmailStr
    contact_number: str = Field(min_length=7, max_length=20)
    password: str = Field(min_length=8, max_length=128)
    employee_no: Optional[str] = Field(default=None, max_length=30)


class SchoolUpdate(BaseModel):
    """What a principal may change about their own school."""

    name: Optional[str] = Field(default=None, min_length=3, max_length=200)
    short_name: Optional[str] = Field(default=None, max_length=40)
    address: Optional[str] = Field(default=None, max_length=255)
    city: Optional[str] = Field(default=None, min_length=2, max_length=100)
    province: Optional[str] = Field(default=None, max_length=100)
    region: Optional[str] = Field(default=None, max_length=100)
    contact_number: Optional[str] = Field(default=None, max_length=30)
    contact_email: Optional[EmailStr] = None

    tracks_units: Optional[bool] = None
    unit_label_singular: Optional[str] = Field(default=None, max_length=40)
    unit_label_plural: Optional[str] = Field(default=None, max_length=40)
    default_units_per_subject: Optional[int] = Field(default=None, ge=1, le=200)

    passing_mark: Optional[float] = Field(default=None, ge=1, le=100)
    grading_periods: Optional[int] = Field(default=None, ge=1, le=6)
    grading_period_label: Optional[str] = Field(default=None, max_length=30)

    # Deliberately absent: school_type and status. The first is what the school
    # was registered as and changing it should go through the operator; the
    # second is the operator's decision alone.


class SchoolResponse(ORMModel):
    school_id: int
    name: str
    short_name: Optional[str] = None
    school_type: str
    address: str = ""
    city: str = ""
    province: Optional[str] = None
    region: Optional[str] = None
    contact_number: Optional[str] = None
    contact_email: Optional[str] = None

    tracks_units: bool = False
    unit_label_singular: str = "Module"
    unit_label_plural: str = "Modules"
    default_units_per_subject: int = 12

    passing_mark: float = 75.0
    grading_periods: int = 4
    grading_period_label: str = "Quarter"

    status: str = "pending"
    rejection_reason: Optional[str] = None
    created_at: Optional[datetime] = None
    approved_at: Optional[datetime] = None


class SchoolAdminView(SchoolResponse):
    """What the system operator sees in the approvals list.

    Counts only — how many staff, how many students. Never a name, a grade or
    an attendance figure. The operator approves schools; they do not read the
    records inside them.
    """

    principal_name: Optional[str] = None
    principal_email: Optional[str] = None
    staff_count: int = 0
    student_count: int = 0


class SchoolDirectoryEntry(BaseModel):
    """What an unauthenticated teacher sees when choosing their school.

    Name and city only — enough to tell two St Mary's Academies apart, and
    nothing that is not already public about a school.
    """

    school_id: int
    name: str
    city: str = ""
    school_type: str = "private"


class SchoolDecision(BaseModel):
    action: Literal["approve", "reject", "deactivate", "reactivate"]
    reason: Optional[str] = Field(default=None, max_length=500)
