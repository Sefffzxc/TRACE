"""Pydantic models for the academic structure."""
from __future__ import annotations

import uuid
from datetime import date
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ── School year ───────────────────────────────────────────────────────────────
class SchoolYearBase(BaseModel):
    year: str = Field(max_length=20, examples=["2025-2026"])
    start_date: date
    end_date: date
    is_current: bool = False


class SchoolYearCreate(SchoolYearBase):
    pass


class SchoolYearUpdate(BaseModel):
    year: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    is_current: Optional[bool] = None


class SchoolYearResponse(ORMModel, SchoolYearBase):
    school_year_id: int


# ── Grade level ───────────────────────────────────────────────────────────────
class GradeLevelBase(BaseModel):
    level: str = Field(max_length=10)
    name: str = Field(max_length=30)
    sort_order: int = 0


class GradeLevelCreate(GradeLevelBase):
    pass


class GradeLevelUpdate(BaseModel):
    level: Optional[str] = None
    name: Optional[str] = None
    sort_order: Optional[int] = None


class GradeLevelResponse(ORMModel, GradeLevelBase):
    grade_level_id: int
    section_count: int = 0
    student_count: int = 0


# ── Section ───────────────────────────────────────────────────────────────────
class SectionBase(BaseModel):
    grade_level_id: int
    section_code: str = Field(max_length=20)
    name: str = Field(max_length=40)
    room: Optional[str] = Field(default=None, max_length=40)
    capacity: int = 40


class SectionCreate(SectionBase):
    pass


class SectionUpdate(BaseModel):
    grade_level_id: Optional[int] = None
    section_code: Optional[str] = None
    name: Optional[str] = None
    room: Optional[str] = None
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


# ── Class schedule ────────────────────────────────────────────────────────────
class ClassScheduleBase(BaseModel):
    school_year_id: int
    section_id: int
    subject_id: int
    teacher_id: uuid.UUID
    day_of_week: str
    start_time: str = Field(pattern=r"^\d{2}:\d{2}$")
    end_time: str = Field(pattern=r"^\d{2}:\d{2}$")
    room: str = "TBA"


class ClassScheduleCreate(ClassScheduleBase):
    pass


class ClassScheduleUpdate(BaseModel):
    section_id: Optional[int] = None
    subject_id: Optional[int] = None
    teacher_id: Optional[uuid.UUID] = None
    day_of_week: Optional[str] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    room: Optional[str] = None


class ClassScheduleResponse(ORMModel, ClassScheduleBase):
    schedule_id: int
    section_name: Optional[str] = None
    subject_name: Optional[str] = None
    teacher_name: Optional[str] = None


# ── Teacher availability ──────────────────────────────────────────────────────
class AvailabilityBase(BaseModel):
    teacher_id: uuid.UUID
    day: str
    start_time: str
    end_time: str
    location: str = "Faculty Room"
    is_active: bool = True


class AvailabilityCreate(AvailabilityBase):
    pass


class AvailabilityResponse(ORMModel, AvailabilityBase):
    availability_id: int
    teacher_name: Optional[str] = None
