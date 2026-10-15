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
    school_id = Column(
        Integer,
        ForeignKey("schools.school_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Unique per school: two schools may both number their first student S0001.
    login_id = Column(String(20), nullable=True, index=True)

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

    __table_args__ = (
        UniqueConstraint("school_id", "login_id", name="uq_login_id_per_school"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Student {self.last_name}, {self.first_name}>"


class StudentEnrollment(Base):
    """One student, one school year, one section."""

    __tablename__ = "student_enrollments"

    enrollment_id = Column(Integer, primary_key=True, index=True)
    school_id = Column(
        Integer,
        ForeignKey("schools.school_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
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
    school_id = Column(
        Integer,
        ForeignKey("schools.school_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
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
    # Which term this record covers. A subject has one record PER TERM, not
    # one record: the final grade is the mean of the terms, so a system that
    # kept only the latest could never compute it — and, worse, encoding
    # Term 2 would silently overwrite Term 1.
    grading_period = Column(Integer, nullable=False, default=1, index=True)
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
    # What the teacher types: one score per assessment, as JSON arrays, in the
    # order the columns appear on the class record. A null inside an array is
    # a cell left blank, which is not the same as a zero and is never treated
    # as one. The arrays are the record of truth; everything below them is
    # derived from these by app.services.grading and recomputed on every save,
    # so a stored grade can never drift away from the scores behind it.
    ww_scores = Column(Text, nullable=True)      # JSON array, Written Works
    pt_scores = Column(Text, nullable=True)      # JSON array, Performance Tasks
    # [ST1, ST2, Term Examination]. One array rather than three columns
    # because the three are one component of the grade, each with its own
    # share of it.
    exam_scores = Column(Text, nullable=True)

    # Derived, in the order the class record prints them.
    ww_total_raw = Column(Float, nullable=True)
    ww_percent = Column(Float, nullable=True)
    ww_weighted = Column(Float, nullable=True)
    pt_total_raw = Column(Float, nullable=True)
    pt_percent = Column(Float, nullable=True)
    pt_weighted = Column(Float, nullable=True)
    # The three per-examination weighted figures the sheet prints as
    # WS ST1 / WS ST2 / WS TE, as a JSON array.
    exam_parts = Column(Text, nullable=True)
    exam_percent = Column(Float, nullable=True)
    exam_weighted = Column(Float, nullable=True)
    initial_grade = Column(Float, nullable=True)
    transmuted_grade = Column(Integer, nullable=True)
    descriptor = Column(String(20), nullable=True)
    # Coursework banked before the examinations, and the most it could be
    # worth. Stored together so a learner's standing can be put on the same
    # 0-100 axis as a grade without re-deriving which weights applied.
    pre_exam_weighted = Column(Float, nullable=True)
    pre_exam_max = Column(Float, nullable=True)
    # False while any assessment is still unmarked. A grade computed from a
    # part-filled record is provisional, and the interface says so rather
    # than presenting it as settled.
    record_complete = Column(Boolean, nullable=False, default=False)

    # Kept because schools use them operationally, though the model trained on
    # the class-record export reads neither: that export carries no attendance
    # column at all.
    absences = Column(Integer, nullable=True)
    extracurricular_count = Column(Integer, nullable=True)

    created_at = Column(DateTime(timezone=True), default=_utcnow)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    student = relationship("Student", back_populates="paces")
    enrollment = relationship("StudentEnrollment", back_populates="paces")

    __table_args__ = (
        UniqueConstraint(
            "enrollment_id", "subject", "grading_period",
            name="uq_pace_per_subject_term",
        ),
    )


class ClassRecordSetup(Base):
    """The header of one class record: the HIGHEST POSSIBLE SCORE row and the
    component weights.

    These belong to the sheet, not to a learner — every learner in a section
    is marked out of the same totals — so they are stored once per section,
    subject and grading period rather than copied onto every row.

    Weights are per record because the school's own files use both 30/50/20
    and 40/40/20 depending on the quarter, so they cannot be a constant and
    cannot even be a per-school setting.
    """

    __tablename__ = "class_record_setups"

    setup_id = Column(Integer, primary_key=True, index=True)
    school_id = Column(
        Integer,
        ForeignKey("schools.school_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    section_id = Column(
        Integer, ForeignKey("sections.section_id", ondelete="CASCADE"), nullable=False
    )
    subject_id = Column(
        Integer, ForeignKey("subjects.subject_id", ondelete="CASCADE"), nullable=False
    )
    # Three terms, not four quarters.
    grading_period = Column(Integer, nullable=False, default=1)

    # Which of the two class records this subject uses. The template decides
    # the default weights: Science, Mathematics, English, Filipino and
    # Araling Panlipunan run 20/50/30, while Music and Arts and Physical
    # Education and Health run 20/60/20.
    template = Column(String(10), nullable=False, default="core")

    # JSON arrays of the highest possible score per item. Their lengths are
    # what decides how many columns the class record draws.
    ww_highest = Column(Text, nullable=False, default="[]")
    pt_highest = Column(Text, nullable=False, default="[]")
    # [ST1, ST2, Term Examination]
    exam_highest = Column(Text, nullable=False, default="[]")

    ww_weight = Column(Float, nullable=False, default=20.0)
    pt_weight = Column(Float, nullable=False, default=50.0)
    ex_weight = Column(Float, nullable=False, default=30.0)
    # How the examinations component is split between ST1, ST2 and the term
    # examination. The sheet ships 30/30/40 and a school may change it.
    exam_subweights = Column(Text, nullable=False, default="[30, 30, 40]")

    updated_by = Column(GUID(), ForeignKey("staff.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    __table_args__ = (
        UniqueConstraint(
            "section_id", "subject_id", "grading_period", name="uq_class_record_setup"
        ),
    )


class EarlyWarning(Base):
    """A risk flag raised for a student in one subject."""

    __tablename__ = "early_warnings"

    warning_id = Column(Integer, primary_key=True, index=True)
    school_id = Column(
        Integer,
        ForeignKey("schools.school_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
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
    # Where the learner stands, on the same 0-100 axis as a grade: the term
    # grade once the record is complete, the coursework banked before that.
    # This is what the flag was raised on, so it is stored with the flag
    # rather than looked up again later from a record that may have moved.
    standing = Column(Float, nullable=True)
    # "grades", "coursework", "units" or "year" — which reading produced the flag,
    # so the list can say whether a grade is settled or still provisional.
    basis = Column(String(12), nullable=False, default="grades")
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
    school_id = Column(
        Integer,
        ForeignKey("schools.school_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
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

    # Model output. risk_score is the chance of failing (final grade below
    # 75), 0–1; HIGH when it reaches used_threshold.
    risk_score = Column(Float, nullable=False, default=0.0)
    risk_level = Column(String(10), nullable=False, default="LOW")  # HIGH | LOW
    # The final-grade model's answer: the expected final grade and its likely
    # (80%) range. Nullable: rows written by earlier models never had one.
    predicted_final = Column(Float, nullable=True)
    range_low = Column(Float, nullable=True)
    range_high = Column(Float, nullable=True)
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
    school_id = Column(
        Integer,
        ForeignKey("schools.school_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
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
