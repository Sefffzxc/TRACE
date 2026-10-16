"""Academic structure: school years, grade levels, sections and subjects."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.session import Base
from app.db.types import GUID


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SchoolYear(Base):
    __tablename__ = "school_years"

    school_year_id = Column(Integer, primary_key=True, index=True)
    school_id = Column(
        Integer,
        ForeignKey("schools.school_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Unique per school, not globally: every school has a "2025-2026".
    year = Column(String(20), nullable=False, index=True)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    is_current = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    enrollments = relationship("StudentEnrollment", back_populates="school_year")

    __table_args__ = (
        UniqueConstraint("school_id", "year", name="uq_year_per_school"),
    )


class GradeLevel(Base):
    __tablename__ = "grade_levels"

    grade_level_id = Column(Integer, primary_key=True, index=True)
    school_id = Column(
        Integer,
        ForeignKey("schools.school_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    level = Column(String(10), nullable=False, index=True)  # "7"
    name = Column(String(30), nullable=False)  # "Grade 7"
    sort_order = Column(Integer, nullable=False, default=0)

    sections = relationship(
        "Section", back_populates="grade_level", cascade="all, delete-orphan"
    )
    subjects = relationship(
        "Subject", back_populates="grade_level", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("school_id", "level", name="uq_level_per_school"),
    )


class Section(Base):
    __tablename__ = "sections"

    section_id = Column(Integer, primary_key=True, index=True)
    school_id = Column(
        Integer,
        ForeignKey("schools.school_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    grade_level_id = Column(
        Integer,
        ForeignKey("grade_levels.grade_level_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    section_code = Column(String(20), nullable=False, index=True)
    name = Column(String(40), nullable=False)
    room = Column(String(40), nullable=True)
    capacity = Column(Integer, nullable=False, default=40)

    grade_level = relationship("GradeLevel", back_populates="sections")
    assignments = relationship(
        "TeacherAssignment", back_populates="section", cascade="all, delete-orphan"
    )
    enrollments = relationship("StudentEnrollment", back_populates="section")

    __table_args__ = (
        UniqueConstraint("school_id", "section_code", name="uq_section_code_per_school"),
    )


class Subject(Base):
    __tablename__ = "subjects"

    subject_id = Column(Integer, primary_key=True, index=True)
    school_id = Column(
        Integer,
        ForeignKey("schools.school_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    grade_level_id = Column(
        Integer,
        ForeignKey("grade_levels.grade_level_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    subject_name = Column(String(255), nullable=False, index=True)
    subject_code = Column(String(50), nullable=False, index=True)
    # Only meaningful where the school tracks lesson units (School of Tomorrow
    # calls them PACEs; another school may call them modules, or not track them
    # at all). See School.tracks_units.
    total_paces = Column(Integer, nullable=False, default=12)
    is_active = Column(Boolean, nullable=False, default=True)

    grade_level = relationship("GradeLevel", back_populates="subjects")

    __table_args__ = (
        UniqueConstraint("school_id", "subject_code", name="uq_subject_code_per_school"),
    )


class TeacherAssignment(Base):
    """Which teacher handles which section (optionally for one subject)."""

    __tablename__ = "teacher_assignments"

    assignment_id = Column(Integer, primary_key=True, index=True)
    school_id = Column(
        Integer,
        ForeignKey("schools.school_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    teacher_id = Column(
        GUID(), ForeignKey("staff.id", ondelete="CASCADE"), nullable=False, index=True
    )
    section_id = Column(
        Integer,
        ForeignKey("sections.section_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    subject_id = Column(
        Integer,
        ForeignKey("subjects.subject_id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    is_adviser = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    teacher = relationship("Staff", backref="assignments")
    section = relationship("Section", back_populates="assignments")
    subject = relationship("Subject", backref="assignments")

    __table_args__ = (
        UniqueConstraint(
            "teacher_id", "section_id", "subject_id", name="uq_teacher_section_subject"
        ),
    )


class ClassSchedule(Base):
    """A weekly timetable slot: teacher + subject + section + day + time."""
    # No longer used: the timetable was removed from TRACE. The model stays so
    # the table and any rows already in it are left alone rather than
    # dropped; nothing reads or writes it.

    __tablename__ = "class_schedules"

    schedule_id = Column(Integer, primary_key=True, index=True)
    school_id = Column(
        Integer,
        ForeignKey("schools.school_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    school_year_id = Column(
        Integer,
        ForeignKey("school_years.school_year_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    section_id = Column(
        Integer,
        ForeignKey("sections.section_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    subject_id = Column(
        Integer,
        ForeignKey("subjects.subject_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    teacher_id = Column(
        GUID(), ForeignKey("staff.id", ondelete="CASCADE"), nullable=False, index=True
    )
    day_of_week = Column(String(20), nullable=False)  # Monday … Friday
    start_time = Column(String(5), nullable=False)  # "08:00"
    end_time = Column(String(5), nullable=False)  # "09:00"
    room = Column(String(50), nullable=False, default="TBA")
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    section = relationship("Section", backref="schedules")
    subject = relationship("Subject", backref="schedules")
    teacher = relationship("Staff", backref="schedules")
    school_year = relationship("SchoolYear", backref="schedules")

    __table_args__ = (
        Index("ix_sched_section_slot", "section_id", "day_of_week", "start_time"),
        Index("ix_sched_teacher_slot", "teacher_id", "day_of_week", "start_time"),
    )


class TeacherAvailability(Base):
    """Consultation / free periods a teacher publishes."""
    # No longer used: the timetable was removed from TRACE. The model stays so
    # the table and any rows already in it are left alone rather than
    # dropped; nothing reads or writes it.

    __tablename__ = "teacher_availabilities"

    availability_id = Column(Integer, primary_key=True, index=True)
    school_id = Column(
        Integer,
        ForeignKey("schools.school_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    teacher_id = Column(
        GUID(), ForeignKey("staff.id", ondelete="CASCADE"), nullable=False, index=True
    )
    day = Column(String(20), nullable=False)
    start_time = Column(String(5), nullable=False)
    end_time = Column(String(5), nullable=False)
    location = Column(String(100), nullable=False, default="Faculty Room")
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    teacher = relationship("Staff", backref="availabilities")
