"""Recorded subject grades and their current rule-based risk reading."""
from __future__ import annotations

import math
from types import SimpleNamespace

from app.services import grading, risk


def completed_grade(record):
    """A partial record can carry computed figures without being a finished term."""
    value = getattr(record, "transmuted_grade", None)
    if not getattr(record, "record_complete", False) or value is None:
        return None
    try:
        grade = float(value)
    except (TypeError, ValueError):
        return None
    return grade if math.isfinite(grade) and 0 <= grade <= 100 else None


def summarise_grades(records, period_count: int, passing_mark: float) -> dict:
    """Average completed periods once; transmutation already happened per term.

    A final grade requires every configured period. Until then, the average
    describes completed work only. Future and incomplete periods never count
    as zero or as completed grades.
    """
    periods = {row.grading_period: row for row in records
               if row.grading_period is not None and 1 <= row.grading_period <= period_count}
    grades = [completed_grade(periods.get(n)) for n in range(1, period_count + 1)]
    done = [grade for grade in grades if grade is not None]
    average = round(sum(done) / len(done), 2) if done else None
    final = average if len(done) == period_count else None
    level = None
    if periods:
        # Before the first finished period, read actual coursework rather than
        # treating an unfinished term's computed grade as a final term result.
        evidence = [row for row in periods.values() if any(
            getattr(row, key, None) is not None
            for key in ("pre_exam_weighted", "ww_percent", "pt_percent", "exam_percent"))
            or completed_grade(row) is not None]
        if evidence:
            current = max(evidence, key=lambda row: row.grading_period)
            reading = SimpleNamespace(
                transmuted_grade=completed_grade(current),
                pre_exam_weighted=getattr(current, "pre_exam_weighted", None),
                pre_exam_max=getattr(current, "pre_exam_max", None),
                current_pace=0, expected_pace=0, total_paces=0,
            )
            evaluated = risk.evaluate_year(reading, grades, 80.0, passing_mark, period_count)
            # A lone examination has no coursework standing yet.
            if done or evaluated.get("standing") is not None:
                level = evaluated["risk_level"]
    return {
        "term_grades": grades,
        "completed_periods": len(done),
        "current_average": round(average, 2) if average is not None else None,
        "final_grade": round(final, 2) if final is not None else None,
        "descriptor": grading.descriptor(final) if final is not None else None,
        "result": ("Passed" if final >= passing_mark else "Failed") if final is not None else "Pending",
        "risk_level": level,
    }
