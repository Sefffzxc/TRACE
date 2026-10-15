"""Pydantic models for students, enrolments, PACE records and warnings."""
from __future__ import annotations

import json
import uuid
from datetime import date, datetime
from typing import List, Literal, Optional

from pydantic import (BaseModel, ConfigDict, EmailStr, Field, field_validator,
                      model_validator)


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ── Student ───────────────────────────────────────────────────────────────────
class StudentBase(BaseModel):
    first_name: str = Field(max_length=50)
    middle_name: Optional[str] = Field(default=None, max_length=50)
    last_name: str = Field(max_length=50)
    birth_date: Optional[date] = None
    gender: Literal["Male", "Female"] = "Male"
    address: str = ""
    photo: Optional[str] = None

    guardian_first_name: str = ""
    guardian_mid_name: Optional[str] = None
    guardian_last_name: str = ""
    guardian_contact: str = ""
    guardian_relationship: str = "Parent"
    guardian_email: Optional[EmailStr] = None


class StudentCreate(StudentBase):
    # Optional enrolment written in the same request
    grade_level_id: Optional[int] = None
    section_id: Optional[int] = None
    school_year_id: Optional[int] = None


class StudentUpdate(BaseModel):
    first_name: Optional[str] = None
    middle_name: Optional[str] = None
    last_name: Optional[str] = None
    birth_date: Optional[date] = None
    gender: Optional[str] = None
    address: Optional[str] = None
    photo: Optional[str] = None
    guardian_first_name: Optional[str] = None
    guardian_mid_name: Optional[str] = None
    guardian_last_name: Optional[str] = None
    guardian_contact: Optional[str] = None
    guardian_relationship: Optional[str] = None
    guardian_email: Optional[EmailStr] = None
    is_active: Optional[bool] = None


class StudentResponse(ORMModel, StudentBase):
    student_id: int
    login_id: Optional[str] = None
    is_active: bool = True
    created_at: Optional[datetime] = None

    # Flattened current-enrolment context, filled in by the service layer
    full_name: Optional[str] = None
    enrollment_id: Optional[int] = None
    grade_level_id: Optional[int] = None
    grade_level_name: Optional[str] = None
    section_id: Optional[int] = None
    section_name: Optional[str] = None
    school_year_id: Optional[int] = None
    adviser_name: Optional[str] = None

    # Rolled up across the learner's subjects. The risk is the WORST
    # subject's, not an average: a learner excellent in three subjects and
    # failing the fourth still needs intervention, and averaging that away
    # would hide them.
    risk_level: str = "low"
    risk_score: float = 0.0
    subjects_tracked: int = 0
    subjects_flagged: int = 0
    # Standing is on the same 0-100 axis as a grade: the term grade once a
    # record is complete, the coursework banked before that.
    lowest_standing: Optional[float] = None
    average_standing: Optional[float] = None
    lowest_term_grade: Optional[int] = None
    weakest_subject: Optional[str] = None
    # What the learner must average over the terms still to come, in the
    # subject they are weakest in, to finish it at the line. None before
    # any term is finished and once the year is over.
    required_in_weakest: Optional[float] = None
    terms_done: int = 0
    # Only meaningful at a school that counts booklets; a DepEd school
    # leaves them at zero and its screens do not show them.
    pace_percent: float = 0.0
    paces_behind: int = 0


# ── Enrolment ─────────────────────────────────────────────────────────────────
class EnrollmentBase(BaseModel):
    student_id: int
    grade_level_id: int
    section_id: int
    school_year_id: int
    enrollment_date: Optional[date] = None
    is_active: bool = True


class EnrollmentCreate(EnrollmentBase):
    pass


class EnrollmentUpdate(BaseModel):
    grade_level_id: Optional[int] = None
    section_id: Optional[int] = None
    school_year_id: Optional[int] = None
    next_grade_level_id: Optional[int] = None
    is_active: Optional[bool] = None
    end_of_year_status: Optional[
        Literal["Promoted", "Retained", "Dropped", "Graduated"]
    ] = None


class EnrollmentResponse(ORMModel, EnrollmentBase):
    enrollment_id: int
    next_grade_level_id: Optional[int] = None
    end_of_year_status: Optional[str] = None

    # Display labels filled in by the router. These must NOT be named after a
    # relationship on StudentEnrollment (student, grade_level, section,
    # school_year, paces, warnings) — `model_validate` reads attributes off the
    # ORM row, and touching an unloaded relationship in async SQLAlchemy raises
    # MissingGreenlet. Hence the `_name` suffix throughout.
    student_name: Optional[str] = None
    grade_level_name: Optional[str] = None
    section_name: Optional[str] = None
    school_year_name: Optional[str] = None


# ── PACE ──────────────────────────────────────────────────────────────────────
class PaceBase(BaseModel):
    student_id: int
    enrollment_id: int
    subject: str = Field(max_length=100)
    subject_id: Optional[int] = None
    current_pace: int = 0
    expected_pace: int = 0
    total_paces: int = 12
    attendance: float = 100.0
    average_score: float = 0.0
    remarks: Optional[str] = None


ItemScores = Optional[List[Optional[float]]]


class AssessmentInput(BaseModel):
    """What a teacher types into one row of the class record.

    One score per assessment, in column order. A null inside an array is a
    cell left blank; it is stored as blank, never as zero, and the derived
    figures are computed from the recorded cells only.
    """

    ww_scores: ItemScores = None
    pt_scores: ItemScores = None
    # [ST1, ST2, Term Examination]
    exam_scores: ItemScores = None

    # Operational, not model inputs: the class-record export the model was
    # trained on contains no attendance column.
    absences: Optional[int] = Field(default=None, ge=0, le=200)
    extracurricular_count: Optional[int] = Field(default=None, ge=0, le=50)

    @field_validator("ww_scores", "pt_scores", "exam_scores", mode="before")
    @classmethod
    def _parse_stored(cls, v):
        """Accept the JSON text the database holds as well as a real list.

        The arrays are stored as text so the same schema works on SQLite and
        PostgreSQL, which means a response built straight from an ORM row
        arrives here as a string.
        """
        if isinstance(v, str):
            if not v.strip():
                return None
            try:
                parsed = json.loads(v)
            except ValueError:
                return None
            return parsed if isinstance(parsed, list) else None
        return v

    @field_validator("ww_scores", "pt_scores", "exam_scores")
    @classmethod
    def _sane_scores(cls, v: ItemScores) -> ItemScores:
        if v is None:
            return v
        if len(v) > 40:
            raise ValueError("a component may hold at most 40 assessments")
        for s in v:
            if s is not None and (s < 0 or s > 1000):
                raise ValueError("each score must be between 0 and 1000")
        return v


class AssessmentDerived(BaseModel):
    """Everything computed from the scores above by app.services.grading.

    Read-only. These are recalculated on every save, so a value sent by a
    client is ignored rather than trusted — the grade on screen is always the
    grade the stored scores produce.
    """

    ww_total_raw: Optional[float] = None
    ww_percent: Optional[float] = None
    ww_weighted: Optional[float] = None
    pt_total_raw: Optional[float] = None
    pt_percent: Optional[float] = None
    pt_weighted: Optional[float] = None
    # The three per-examination weighted figures: WS ST1, WS ST2, WS TE.
    exam_parts: ItemScores = None
    exam_percent: Optional[float] = None
    exam_weighted: Optional[float] = None
    initial_grade: Optional[float] = None
    transmuted_grade: Optional[int] = None
    descriptor: Optional[str] = None
    record_complete: bool = False

    @field_validator("exam_parts", mode="before")
    @classmethod
    def _parse_parts(cls, v):
        if isinstance(v, str):
            if not v.strip():
                return None
            try:
                parsed = json.loads(v)
            except ValueError:
                return None
            return parsed if isinstance(parsed, list) else None
        return v


class AssessmentFields(AssessmentInput, AssessmentDerived):
    """Input plus derived, for the endpoints that return a whole record."""


# ── The class-record header: template, highest possible scores, weights ─────
TEMPLATE_DEFAULTS = {
    "core": (20.0, 50.0, 30.0),
    "mapeh": (20.0, 60.0, 20.0),
}


class ClassRecordSetupBase(BaseModel):
    # Which of the two class records this subject uses.
    template: Literal["core", "mapeh"] = "core"
    ww_highest: List[Optional[float]] = Field(default_factory=list)
    pt_highest: List[Optional[float]] = Field(default_factory=list)
    # [ST1, ST2, Term Examination]
    exam_highest: List[Optional[float]] = Field(default_factory=list)
    ww_weight: float = Field(default=20.0, ge=0, le=100)
    pt_weight: float = Field(default=50.0, ge=0, le=100)
    ex_weight: float = Field(default=30.0, ge=0, le=100)
    exam_subweights: List[float] = Field(default_factory=lambda: [30.0, 30.0, 40.0])

    @model_validator(mode="after")
    def _weights_total_100(self):
        total = self.ww_weight + self.pt_weight + self.ex_weight
        if abs(total - 100.0) > 0.01:
            raise ValueError(
                f"the three component weights must total 100 percent, not {total:g}"
            )
        shares = sum(self.exam_subweights)
        if self.exam_subweights and abs(shares - 100.0) > 0.01:
            raise ValueError(
                f"the examination shares must total 100, not {shares:g}"
            )
        return self


class ClassRecordSetupUpdate(ClassRecordSetupBase):
    pass


class ClassRecordSetupResponse(ClassRecordSetupBase):
    section_id: int
    subject_id: int
    grading_period: int


class PaceCreate(PaceBase, AssessmentInput):
    pass


class PaceUpdate(AssessmentInput):
    subject: Optional[str] = None
    current_pace: Optional[int] = None
    expected_pace: Optional[int] = None
    total_paces: Optional[int] = None
    attendance: Optional[float] = None
    average_score: Optional[float] = None
    remarks: Optional[str] = None


class PaceResponse(ORMModel, PaceBase, AssessmentFields):
    pace_id: int
    pace_percent: float = 0.0
    paces_behind: int = 0
    updated_at: Optional[datetime] = None
    student_name: Optional[str] = None
    section_name: Optional[str] = None
    grade_level_name: Optional[str] = None
    risk_level: str = "low"
    # Filled in by the router from the newest ai_risk_predictions row, when
    # there is one. `ai_` prefixed so it can never be confused with the
    # rule-based risk_level above. The threshold, version and
    # partial-input flag travel with the score so the UI never has to assume
    # any of them.
    ai_risk_score: Optional[float] = None
    ai_risk_level: Optional[str] = None
    ai_source: Optional[str] = None
    ai_used_threshold: Optional[float] = None
    ai_model_version: Optional[str] = None
    ai_used_baseline_defaults: Optional[bool] = None
    ai_primary_factor: Optional[str] = None


class BulkPaceEntry(AssessmentInput):
    student_id: int
    enrollment_id: int
    subject: str
    current_pace: int
    expected_pace: int
    total_paces: int = 12
    attendance: float = 100.0
    average_score: float = 0.0


class BulkPaceRequest(BaseModel):
    entries: List[BulkPaceEntry]
    # One save is one class record, so the sheet's header travels with it.
    # Given these three, the server loads the highest possible scores and the
    # weights itself rather than letting a client send the numbers a grade is
    # computed from.
    section_id: Optional[int] = None
    subject_id: Optional[int] = None
    grading_period: Optional[int] = None


# ── Early warning ─────────────────────────────────────────────────────────────
class WarningBase(BaseModel):
    student_id: int
    enrollment_id: Optional[int] = None
    subject: str
    teacher: str = "Unassigned"
    risk_level: Literal["critical", "high", "moderate", "low"]
    risk_score: float = 0.0
    paces_behind: int = 0
    pace_percent: float = 0.0
    # Where the learner stands, on the same 0-100 axis as a grade, and which
    # reading produced it. Attendance used to sit here and no longer does:
    # the class records the system reads carry no attendance column, so it
    # was a constant 100 on every row at every school.
    standing: Optional[float] = None
    # "year" comes from risk.evaluate_year, once at least one term is done.
    basis: Literal["grades", "coursework", "units", "year"] = "grades"
    status: str = "On Track"
    trend: str = "stable"
    last_activity: str = "Today"
    intervention: Optional[str] = None


class WarningCreate(WarningBase):
    pass


class WarningUpdate(BaseModel):
    risk_level: Optional[str] = None
    status: Optional[str] = None
    trend: Optional[str] = None
    intervention: Optional[str] = None
    is_resolved: Optional[bool] = None


class WarningResponse(ORMModel, WarningBase):
    warning_id: int
    is_resolved: bool = False
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    student_name: Optional[str] = None
    grade_level_name: Optional[str] = None
    section_name: Optional[str] = None


# ── AI risk prediction ────────────────────────────────────────────────────────
class AIPredictionResponse(ORMModel):
    """One logged answer from the classifier.

    ``source`` must be shown wherever ``risk_score`` is shown. "fallback" means
    the AI service was unreachable and the rule-based engine produced this row
    instead; it is not a model prediction.
    """

    prediction_id: int
    student_id: int
    enrollment_id: Optional[int] = None
    pace_id: Optional[int] = None
    subject: str = ""

    risk_score: float = 0.0
    risk_level: str = "LOW"
    used_threshold: float = 0.5
    primary_factor: Optional[str] = None
    suggested_action: Optional[str] = None

    source: Literal["model", "fallback"] = "model"
    model_version: str = "unknown"
    used_baseline_defaults: bool = False
    imputed_fields: List[str] = []
    # The exact input the model was given, so any prediction shown in the
    # system can be reproduced by hand.
    features: Optional[dict] = None
    latency_ms: Optional[int] = None
    error: Optional[str] = None
    created_at: Optional[datetime] = None

    # Both columns are stored as JSON text; parse them on the way out.
    @field_validator("imputed_fields", mode="before")
    @classmethod
    def _parse_imputed(cls, value):
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except (TypeError, ValueError):
                return []
            return parsed if isinstance(parsed, list) else []
        return value if value is not None else []

    @field_validator("features", mode="before")
    @classmethod
    def _parse_features(cls, value):
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except (TypeError, ValueError):
                return None
            return parsed if isinstance(parsed, dict) else None
        return value

    # Display labels filled in by the router (see the note on EnrollmentResponse
    # — never name these after a relationship on the ORM row).
    student_name: Optional[str] = None
    section_name: Optional[str] = None
    grade_level_name: Optional[str] = None


class AIStatusResponse(BaseModel):
    enabled: bool
    reachable: bool
    service_url: str
    model_version: Optional[str] = None
    model_loaded: bool = False
    detail: Optional[str] = None
    latency_ms: Optional[int] = None


class RescoreRequest(BaseModel):
    """Re-run the classifier over stored PACE records.

    With no filters it re-scores every PACE record that has an active
    enrolment. Nothing is overwritten — each run appends new prediction rows.
    """

    section_id: Optional[int] = None
    student_id: Optional[int] = None
    subject: Optional[str] = None
    limit: int = Field(default=500, ge=1, le=2000)


class RescoreResponse(BaseModel):
    requested: int
    scored: int
    fallback: int
    source: Literal["model", "fallback", "mixed", "none"] = "none"
    model_version: Optional[str] = None
    detail: Optional[str] = None


# ── Data quality ──────────────────────────────────────────────────────────────
class DataQualityCreate(BaseModel):
    student_id: int
    teacher_id: Optional[uuid.UUID] = None
    student_pace_id: Optional[int] = None
    issue_type: str
    detail: Optional[str] = None


class DataQualityResponse(ORMModel, DataQualityCreate):
    log_id: int
    resolved: bool = False
    resolved_date: Optional[datetime] = None
    created_at: Optional[datetime] = None
    student_name: Optional[str] = None


# ── Dashboard ─────────────────────────────────────────────────────────────────
class DashboardStats(BaseModel):
    total_students: int = 0
    active_enrollments: int = 0
    total_teachers: int = 0
    pending_approvals: int = 0
    total_sections: int = 0
    total_subjects: int = 0
    average_pace: float = 0.0
    # Mean standing across every record: the term grade where one exists,
    # the coursework banked where it does not. It replaces the attendance
    # average, which was a constant 100 because the class records the
    # system reads carry no attendance column.
    average_standing: float = 0.0
    on_track: int = 0
    at_risk: int = 0
    critical: int = 0
    risk_distribution: List[dict] = []
    pace_by_section: List[dict] = []
    pace_by_subject: List[dict] = []
    trend: List[dict] = []
    # Which dashboard to draw. A school that tracks units (PACE booklets) is
    # read on completion; every other school is read on its class record, and
    # the completion figures above are all zero for it.
    tracks_units: bool = False
    unit_label_plural: str = "Modules"
    passing_mark: float = 75.0
    grading_period_label: str = "Quarter"
    standing_by_section: List[dict] = []
    standing_by_subject: List[dict] = []
    standing_by_period: List[dict] = []
    recent_activity: List[dict] = []
    top_concerns: List[dict] = []
    school_year: Optional[str] = None
