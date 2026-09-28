"""The early-warning engine.

Risk is scored 0–100 from three signals, weighted the way the guidance office
weighs them:

    * how many PACE booklets the student is behind   (55 %)
    * how much of the curriculum is finished          (25 %)
    * attendance                                      (20 %)

The score is then bucketed into four levels that drive the badges, the
dashboard counters and the intervention list.
"""
from __future__ import annotations

from typing import Iterable

# Score thresholds (inclusive lower bound)
CRITICAL_AT = 70.0
HIGH_AT = 50.0
MODERATE_AT = 30.0

LEVEL_STATUS = {
    "critical": "Critical",
    "high": "At Risk",
    "moderate": "Warning",
    "low": "On Track",
}

INTERVENTIONS = {
    "critical": "Escalate to the guidance office; schedule a parent conference this week.",
    "high": "Assign daily supervised PACE time and notify the guardian.",
    "moderate": "Add a weekly check-in and a catch-up goal for the lagging subject.",
    "low": "No action needed — keep monitoring.",
}


def pace_percent(current_pace: int, total_paces: int) -> float:
    """Percentage of the year's PACE booklets completed."""
    if total_paces <= 0:
        return 0.0
    return round(min(100.0, max(0.0, (current_pace / total_paces) * 100)), 1)


def paces_behind(current_pace: int, expected_pace: int) -> int:
    """How many booklets short of the expected pace (never negative)."""
    return max(0, int(expected_pace) - int(current_pace))


def risk_score(
    behind: int,
    percent: float,
    attendance: float,
    total_paces: int = 12,
) -> float:
    """Blend the three signals into a single 0–100 score."""
    # Being 4+ booklets behind is treated as fully saturated risk.
    behind_component = min(1.0, behind / 4.0) * 55.0

    # Completion is protective: 100 % finished removes the whole component.
    completion_component = (1.0 - min(1.0, max(0.0, percent) / 100.0)) * 25.0

    # Attendance below 95 % starts to count; below 75 % it is fully saturated.
    att = max(0.0, min(100.0, attendance))
    if att >= 95.0:
        attendance_component = 0.0
    else:
        attendance_component = min(1.0, (95.0 - att) / 20.0) * 20.0

    return round(behind_component + completion_component + attendance_component, 1)


def risk_level(score: float) -> str:
    if score >= CRITICAL_AT:
        return "critical"
    if score >= HIGH_AT:
        return "high"
    if score >= MODERATE_AT:
        return "moderate"
    return "low"


def status_for(level: str) -> str:
    return LEVEL_STATUS.get(level, "On Track")


def intervention_for(level: str) -> str:
    return INTERVENTIONS.get(level, INTERVENTIONS["low"])


def trend_from(previous_percent: float | None, current_percent: float) -> str:
    if previous_percent is None:
        return "stable"
    delta = current_percent - previous_percent
    if delta > 2.5:
        return "improving"
    if delta < -2.5:
        return "declining"
    return "stable"


def evaluate(
    current_pace: int,
    expected_pace: int,
    total_paces: int,
    attendance: float,
) -> dict:
    """One call that produces every derived PACE/risk field."""
    percent = pace_percent(current_pace, total_paces)
    behind = paces_behind(current_pace, expected_pace)
    score = risk_score(behind, percent, attendance, total_paces)
    level = risk_level(score)
    return {
        "pace_percent": percent,
        "paces_behind": behind,
        "risk_score": score,
        "risk_level": level,
        "status": status_for(level),
        "intervention": intervention_for(level),
    }


def summarise(records: Iterable[dict]) -> dict:
    """Roll several per-subject PACE rows up into one student-level view.

    The student's risk is driven by their *worst* subject — a learner who is
    excellent in four subjects and four booklets behind in the fifth still
    needs intervention.
    """
    rows = list(records)
    if not rows:
        return {
            "pace_percent": 0.0,
            "paces_behind": 0,
            "attendance": 100.0,
            "risk_score": 0.0,
            "risk_level": "low",
            "subjects_tracked": 0,
        }

    avg_percent = round(sum(r["pace_percent"] for r in rows) / len(rows), 1)
    avg_attendance = round(sum(r.get("attendance", 100.0) for r in rows) / len(rows), 1)
    worst_behind = max(r["paces_behind"] for r in rows)
    worst_score = max(r["risk_score"] for r in rows)

    return {
        "pace_percent": avg_percent,
        "paces_behind": worst_behind,
        "attendance": avg_attendance,
        "risk_score": worst_score,
        "risk_level": risk_level(worst_score),
        "subjects_tracked": len(rows),
    }
