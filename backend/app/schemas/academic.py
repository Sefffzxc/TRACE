"""Pydantic models for the academic structure."""
from __future__ import annotations

import re
import uuid
from datetime import date
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ── School year ───────────────────────────────────────────────────────────────
_YEAR_LABEL = re.compile(r"^\s*(\d{4})\s*[-–]\s*(\d{4})\s*$")


def school_year_label(value: str) -> str:
    """"2026 - 2027" -> "2026-2027"; anything else is refused.

    A school year is the only thing a principal is asked for: its dates are
    worked out from the label (see school_year_dates), so the label has to be
    a real one.
    """
    match = _YEAR_LABEL.match(value or "")
    if not match or int(match.group(2)) != int(match.group(1)) + 1:
        raise ValueError("Use the format 2026-2027.")
    return f"{match.group(1)}-{match.group(2)}"


def school_year_dates(label: str) -> tuple[date, date]:
    """The span stored for a school year: June 1 of its first year to May 31
    of its second. Nothing in the system reads these beyond putting school
    years in order, so the principal is not asked for them."""
    first = int(label[:4])
    return date(first, 6, 1), date(first + 1, 5, 31)


class SchoolYearBase(BaseModel):
    year: str = Field(max_length=20, examples=["2025-2026"])
    is_current: bool = False


class SchoolYearCreate(SchoolYearBase):
    @field_validator("year")
    @classmethod
    def _label(cls, value: str) -> str:
        return school_year_label(value)


class SchoolYearUpdate(BaseModel):
    year: Optional[str] = None
    is_current: Optional[bool] = None

    @field_validator("year")
    @classmethod
    def _label(cls, value: Optional[str]) -> Optional[str]:
        return None if value is None else school_year_label(value)


class SchoolYearResponse(ORMModel, SchoolYearBase):
    school_year_id: int
    start_date: Optional[date] = None
    end_date: Optional[date] = None


# ── Grade level ───────────────────────────────────────────────────────────────
def grade_level_rank(level: str) -> int:
    """Where a grade level sits in a list: Grade 7 before Grade 10.

    The level is stored as text, which would put "10" before "7", so lists
    are ordered by its number instead. A level with no number ("Kinder")
    goes first.
    """
    digits = re.search(r"[0-9]+", level or "")
    return int(digits.group()) if digits else 0


class GradeLevelBase(BaseModel):
    level: str = Field(max_length=10)
    name: str = Field(max_length=30)


class GradeLevelCreate(GradeLevelBase):
    pass


class GradeLevelUpdate(BaseModel):
    level: Optional[str] = None
    name: Optional[str] = None


class GradeLevelResponse(ORMModel, GradeLevelBase):
    grade_level_id: int
    section_count: int = 0
    student_count: int = 0


# ── Section ───────────────────────────────────────────────────────────────────
class SectionBase(BaseModel):
    grade_level_id: int
    section_code: str = Field(max_length=20)
    name: str = Field(max_length=40)
    capacity: int = 40


class SectionCreate(SectionBase):
    pass


class SectionUpdate(BaseModel):
    grade_level_id: Optional[int] = None
    section_code: Optional[str] = None
    name: Optional[str] = None
    capacity: Optional[int] = None


class SectionResponse(ORMModel, SectionBase):
    section_id: int
    grade_level_name: Optional[str] = None
    enrolled_count: int = 0
    adviser_name: Optional[str] = None


# ── Subject ───────────────────────────────────────────────────────────────────
class SubjectBase(BaseModel):
    grade_level_id: int
    subject_name: str = Field(max_length=255)
    subject_code: str = Field(max_length=50)
    total_paces: int = 12
    is_active: bool = True


class SubjectCreate(SubjectBase):
    pass


class SubjectUpdate(BaseModel):
    grade_level_id: Optional[int] = None
    subject_name: Optional[str] = None
    subject_code: Optional[str] = None
    total_paces: Optional[int] = None
    is_active: Optional[bool] = None


class SubjectResponse(ORMModel, SubjectBase):
    subject_id: int
    grade_level_name: Optional[str] = None


# ── Teacher assignment ────────────────────────────────────────────────────────
class TeacherAssignmentCreate(BaseModel):
    teacher_id: uuid.UUID
    section_id: int
    subject_id: Optional[int] = None
    is_adviser: bool = False


class TeacherAssignmentResponse(ORMModel):
    assignment_id: int
    teacher_id: uuid.UUID
    section_id: int
    subject_id: Optional[int] = None
    is_adviser: bool
    teacher_name: Optional[str] = None
    section_name: Optional[str] = None
    subject_name: Optional[str] = None
