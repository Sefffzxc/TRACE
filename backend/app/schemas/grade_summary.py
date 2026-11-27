"""Read-only subject summaries, including learners without assessment records."""
from typing import Literal

from pydantic import BaseModel

from app.schemas.students import AIPredictionResponse


class GradeSummaryRow(BaseModel):
    student_id: int
    enrollment_id: int
    student_name: str
    login_id: str | None = None
    term_grades: list[float | None]
    completed_periods: int
    current_average: float | None
    final_grade: float | None
    descriptor: str | None
    result: Literal["Pending", "Passed", "Failed"]
    risk_level: Literal["critical", "high", "moderate", "low"] | None
    ai: AIPredictionResponse | None = None


class GradeSummaryResponse(BaseModel):
    school_year_id: int
    section_id: int
    section_name: str
    subject_id: int
    subject: str
    period_count: int
    period_label: str
    passing_mark: float
    rows: list[GradeSummaryRow]
