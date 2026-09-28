"""Students, enrolment records, PACE progress and early-warning flags."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.session import Base
from app.db.types import GUID


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Student(Base):
    __tablename__ = "students"

    student_id = Column(Integer, primary_key=True, index=True)
    login_id = Column(String(20), unique=True, nullable=True, index=True)  # "S0001"

    first_name = Column(String(50), nullable=False)
    middle_name = Column(String(50), nullable=True)
    last_name = Column(String(50), nullable=False)
    birth_date = Column(Date, nullable=True)
    gender = Column(String(10), nullable=False, default="Male")  # Male | Female
    address = Column(String(255), nullable=False, default="")
    photo = Column(Text, nullable=True)

    guardian_first_name = Column(String(50), nullable=False, default="")
    guardian_mid_name = Column(String(50), nullable=True)
    guardian_last_name = Column(String(50), nullable=False, default="")
    guardian_contact = Column(String(20), nullable=False, default="")
    guardian_relationship = Column(String(20), nullable=False, default="Parent")
    guardian_email = Column(String(255), nullable=True)

    is_active = Column(Boolean, nullable=False, default=True)
    created_by = Column(GUID(), ForeignKey("staff.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    enrollments = relationship(
        "StudentEnrollment", back_populates="student", cascade="all, delete-orphan"
    )
    paces = relationship(
        "StudentPace", back_populates="student", cascade="all, delete-orphan"
    )
    warnings = relationship(
        "EarlyWarning", back_populates="student", cascade="all, delete-orphan"
    )

    @property
    def full_name(self) -> str:
        middle = f" {self.middle_name}" if self.middle_name else ""
        return f"{self.first_name}{middle} {self.last_name}"

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Student {self.last_name}, {self.first_name}>"


class StudentEnrollment(Base):
    """One student, one school year, one section."""

    __tablename__ = "student_enrollments"

    enrollment_id = Column(Integer, primary_key=True, index=True)
    student_id = Column(
        Integer,
        ForeignKey("students.student_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    grade_level_id = Column(
        Integer,
        ForeignKey("grade_levels.grade_level_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    section_id = Column(
        Integer,
        ForeignKey("sections.section_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    school_year_id = Column(
        Integer,
        ForeignKey("school_years.school_year_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    next_grade_level_id = Column(
        Integer,
        ForeignKey("grade_levels.grade_level_id", ondelete="SET NULL"),
        nullable=True,
    )
    enrolled_by = Column(GUID(), ForeignKey("staff.id", ondelete="SET NULL"), nullable=True)
    enrollment_date = Column(Date, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    # Promoted | Retained | Dropped | Graduated
    end_of_year_status = Column(String(20), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    student = relationship("Student", back_populates="enrollments")
    grade_level = relationship("GradeLevel", foreign_keys=[grade_level_id])
    next_grade_level = relationship("GradeLevel", foreign_keys=[next_grade_level_id])
    section = relationship("Section", back_populates="enrollments")
    school_year = relationship("SchoolYear", back_populates="enrollments")
    paces = relationship(
        "StudentPace", back_populates="enrollment", cascade="all, delete-orphan"
    )
    warnings = relationship(
        "EarlyWarning", back_populates="enrollment", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("student_id", "school_year_id", name="uq_enrollment_per_year"),
    )


class StudentPace(Base):
    """Progress of one student through one subject's PACE booklets.

    ``pace_percent`` is how much of the year's curriculum is finished;
    ``paces_behind`` is how many booklets short of the expected pace the
    student is at this point in the school year.
    """

    __tablename__ = "student_paces"

    pace_id = Column(Integer, primary_key=True, index=True)
    student_id = Column(
        Integer,
        ForeignKey("students.student_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    enrollment_id = Column(
        Integer,
        ForeignKey("student_enrollments.enrollment_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    subject_id = Column(
        Integer, ForeignKey("subjects.subject_id", ondelete="SET NULL"), nullable=True
    )
    subject = Column(String(100), nullable=False)  # denormalised display name
    current_pace = Column(Integer, nullable=False, default=0)
    expected_pace = Column(Integer, nullable=False, default=0)
    total_paces = Column(Integer, nullable=False, default=12)
    pace_percent = Column(Float, nullable=False, default=0.0)
    paces_behind = Column(Integer, nullable=False, default=0)
    attendance = Column(Float, nullable=False, default=100.0)
    average_score = Column(Float, nullable=False, default=0.0)
    remarks = Column(Text, nullable=True)
    recorded_by = Column(GUID(), ForeignKey("staff.id", ondelete="SET NULL"), nullable=True)

    # ── Assessment record (DepEd class record) ────────────────────────────
    # These are the columns the risk model was trained on. Every one is
    # nullable on purpose: a teacher may encode PACE progress before the
    # quarter's assessments are complete. When a value is missing the AI
    # service substitutes the training median and says so, and the stored
    # prediction carries `used_baseline_defaults = True` — so an incomplete
    # class record produces a visibly weaker prediction rather than a
    # confident one built on invented numbers.
    absences = Column(Integer, nullable=True)           # No of Absences
    ww_total_raw = Column(Float, nullable=True)         # Written Work, raw total
    ww_percent = Column(Float, nullable=True)           # Written Work, % score
    pt_total_raw = Column(Float, nullable=True)         # Performance Tasks, raw total
    pt_percent = Column(Float, nullable=True)           # Performance Tasks, % score
    missed_ww_items = Column(Integer, nullable=True)
    missed_pt_items = Column(Integer, nullable=True)
    extracurricular_count = Column(Integer, nullable=True)

    created_at = Column(DateTime(timezone=True), default=_utcnow)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    student = relationship("Student", back_populates="paces")
    enrollment = relationship("StudentEnrollment", back_populates="paces")

    __table_args__ = (
        UniqueConstraint("enrollment_id", "subject", name="uq_pace_per_subject"),
    )


class EarlyWarning(Base):
    """A risk flag raised for a student in one subject."""

    __tablename__ = "early_warnings"

    warning_id = Column(Integer, primary_key=True, index=True)
    student_id = Column(
        Integer,
        ForeignKey("students.student_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    enrollment_id = Column(
        Integer,
        ForeignKey("student_enrollments.enrollment_id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    subject = Column(String(100), nullable=False)
    teacher = Column(String(120), nullable=False, default="Unassigned")
    risk_level = Column(String(20), nullable=False)  # critical|high|moderate|low
    risk_score = Column(Float, nullable=False, default=0.0)  # 0–100
    paces_behind = Column(Integer, nullable=False, default=0)
    pace_percent = Column(Float, nullable=False, default=0.0)
    attendance = Column(Float, nullable=False, default=100.0)
    status = Column(String(20), nullable=False)  # Critical|At Risk|Warning|On Track
    trend = Column(String(20), nullable=False, default="stable")
    last_activity = Column(String(100), nullable=False, default="Today")
    intervention = Column(Text, nullable=True)
    is_resolved = Column(Boolean, nullable=False, default=False)
    resolved_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), default=_utcnow)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    student = relationship("Student", back_populates="warnings")
    enrollment = relationship("StudentEnrollment", back_populates="warnings")


class AIRiskPrediction(Base):
    """One stored answer from the machine-learning risk classifier.

    Kept apart from ``early_warnings`` on purpose. The early-warning table is
    the school's own rule-based judgement (PACE booklets behind, completion,
    attendance) and staff resolve rows in it. This table is a log of what the
    model said, when, on what input, and which model version said it. Nothing
    here is ever edited — a re-score writes a new row, so the history of
    predictions for a student stays intact and every figure in the thesis can
    be traced back to the exact input that produced it.

    ``source`` is the honest part of the design:

        "model"    the AI service answered
        "fallback" the AI service could not be reached in time and the
                   rule-based engine filled in — this is NOT a model
                   prediction and the UI labels it differently
    """

    __tablename__ = "ai_risk_predictions"

    prediction_id = Column(Integer, primary_key=True, index=True)
    student_id = Column(
        Integer,
        ForeignKey("students.student_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    enrollment_id = Column(
        Integer,
        ForeignKey("student_enrollments.enrollment_id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    pace_id = Column(
        Integer,
        ForeignKey("student_paces.pace_id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    subject = Column(String(100), nullable=False, default="")

    # Model output
    risk_score = Column(Float, nullable=False, default=0.0)  # probability, 0–1
    risk_level = Column(String(10), nullable=False, default="LOW")  # HIGH | LOW
    used_threshold = Column(Float, nullable=False, default=0.5)
    primary_factor = Column(String(120), nullable=True)
    suggested_action = Column(Text, nullable=True)

    # Provenance
    source = Column(String(20), nullable=False, default="model")  # model | fallback
    model_version = Column(String(40), nullable=False, default="unknown")
    used_baseline_defaults = Column(Boolean, nullable=False, default=False)
    imputed_fields = Column(Text, nullable=True)   # JSON array of column names
    features = Column(Text, nullable=True)         # JSON object actually sent
    latency_ms = Column(Integer, nullable=True)
    error = Column(Text, nullable=True)            # why the fallback was used

    created_at = Column(DateTime(timezone=True), default=_utcnow, index=True)

    student = relationship("Student", backref="ai_predictions")
    enrollment = relationship("StudentEnrollment", backref="ai_predictions")

    __table_args__ = (
        Index("ix_ai_pred_pace_created", "pace_id", "created_at"),
        Index("ix_ai_pred_student_created", "student_id", "created_at"),
    )


class DataQualityLog(Base):
    """Encoding problems flagged for follow-up (missing / late PACE entries)."""

    __tablename__ = "data_quality_logs"

    log_id = Column(Integer, primary_key=True, index=True)
    student_id = Column(
        Integer,
        ForeignKey("students.student_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    teacher_id = Column(GUID(), ForeignKey("staff.id", ondelete="SET NULL"), nullable=True)
    student_pace_id = Column(
        Integer, ForeignKey("student_paces.pace_id", ondelete="SET NULL"), nullable=True
    )
    issue_type = Column(String(100), nullable=False)
    detail = Column(Text, nullable=True)
    resolved = Column(Boolean, nullable=False, default=False)
    resolved_date = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    student = relationship("Student", backref="quality_logs")
    teacher = relationship("Staff", backref="quality_logs")
