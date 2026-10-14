"""The early-warning engine.

What a learner is judged on depends on what the school records.

A school keeping a DepEd class record is judged on its grades: the term
grade once the record is complete, and before that the coursework already
banked, expressed on the same 0-100 scale so the two are comparable. The
bands are set from the school's own passing mark, so a learner heading
below it is flagged rather than one who is merely behind on paperwork.

A School of Tomorrow school, which counts PACE booklets rather than
keeping a class record, is judged on how far behind the booklets it is.

Attendance was a third signal and is no longer used at all. The class
records the system is built around carry no attendance column, so the
figure was a constant 100 for every learner at every school — it moved no
score, and leaving it in the formula suggested the system was watching
something it was not.
"""
from __future__ import annotations

from typing import Iterable, Sequence

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
    "high": "Work out the examination mark needed to reach the passing mark and "
            "discuss it with the learner and their guardian now.",
    "moderate": "Add a weekly check-in and a catch-up goal for the weaker component.",
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
    attendance: float = 100.0,
    total_paces: int = 12,
) -> float:
    """Blend the booklet signals into a single 0-100 score.

    For unit-tracking schools only. ``attendance`` is accepted so existing
    callers keep working and is ignored; see the module docstring.
    """
    # Being 4+ booklets behind is treated as fully saturated risk.
    behind_component = min(1.0, behind / 4.0) * 65.0

    # Completion is protective: 100 % finished removes the whole component.
    completion_component = (1.0 - min(1.0, max(0.0, percent) / 100.0)) * 35.0

    return round(behind_component + completion_component, 1)


def standing_score(standing: float, passing_mark: float = 75.0) -> float:
    """Turn a learner's grade standing into the same 0-100 risk scale.

    ``standing`` is a grade, or a grade-shaped figure: the term grade when
    the record is complete, and the coursework banked so far when it is not.

    The bands run off the school's own passing mark, so a school that passes
    at 75 flags the 75-79 band and a school that passes at 80 flags 80-84,
    without anybody editing this file.
    """
    if standing is None:
        return 0.0
    gap = passing_mark - float(standing)
    if gap > 0:
        # Already below the mark. Ten points below is fully saturated.
        return round(min(100.0, CRITICAL_AT + (gap / 10.0) * (100.0 - CRITICAL_AT)), 1)
    # Above the mark: the first five points above it are the warning band,
    # the next five the watch band, and beyond that the learner is clear.
    #
    # Each band stops a tenth short of the next one's floor so that a learner
    # sitting exactly ON a boundary falls in the gentler band. Landing exactly
    # on the passing mark is passing, and reporting it as Critical would be
    # both wrong and the kind of thing a teacher stops trusting the system for.
    above = -gap
    if above < 5.0:
        return round(HIGH_AT + (1.0 - above / 5.0) * (CRITICAL_AT - HIGH_AT - 0.1), 1)
    if above < 10.0:
        return round(
            MODERATE_AT + (1.0 - (above - 5.0) / 5.0) * (HIGH_AT - MODERATE_AT - 0.1), 1
        )
    return round(max(0.0, (MODERATE_AT - 0.1) * (1.0 - min(1.0, (above - 10.0) / 10.0))), 1)


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
    attendance: float = 100.0,
) -> dict:
    """The booklet-based reading, for a school that tracks units.

    ``attendance`` is accepted so existing callers keep working, and ignored.
    """
    percent = pace_percent(current_pace, total_paces)
    behind = paces_behind(current_pace, expected_pace)
    score = risk_score(behind, percent)
    level = risk_level(score)
    return {
        "pace_percent": percent,
        "paces_behind": behind,
        "risk_score": score,
        "risk_level": level,
        "status": status_for(level),
        "intervention": intervention_for(level),
        "basis": "units",
        "standing": None,
    }


def evaluate_record(
    term_grade: float | None,
    coursework_standing: float | None,
    passing_mark: float = 75.0,
) -> dict:
    """The grade-based reading, for a school keeping a class record.

    ``term_grade`` is used when the record is complete. Until then the
    learner is judged on ``coursework_standing`` — what they have banked in
    written works and performance tasks, scaled to 0-100 so it sits on the
    same axis as a grade. Neither is available on an empty record, and an
    empty record is not evidence of anything, so it scores zero.
    """
    standing = term_grade if term_grade is not None else coursework_standing
    score = standing_score(standing, passing_mark)
    level = risk_level(score) if standing is not None else "low"
    return {
        # Kept so the same dict shape serves both readings; a class-record
        # school counts no booklets.
        "pace_percent": 0.0,
        "paces_behind": 0,
        "risk_score": score,
        "risk_level": level,
        "status": status_for(level),
        "intervention": intervention_for(level),
        "basis": "grades" if term_grade is not None else "coursework",
        "standing": None if standing is None else round(float(standing), 2),
    }


def coursework_standing(row) -> float | None:
    """What a record has banked, on the same 0-100 axis as a grade.

    Duck-typed on purpose: this module describes the rules and does not
    import the ORM, so the same function serves a database row, a draft on
    its way in, or a test fixture.
    """
    banked = getattr(row, "pre_exam_weighted", None)
    ceiling = getattr(row, "pre_exam_max", None)
    if banked is None or not ceiling:
        return None
    return round(banked / ceiling * 100, 2)


def evaluate_row(row, passing_mark: float = 75.0) -> dict:
    """Score one record, choosing the reading from what the record holds.

    This is the single definition. The save path, the early-warning sync and
    the student list all call it, which is what stops the Students page and
    the warning list from quietly disagreeing about the same learner.
    """
    has_class_record = (
        getattr(row, "transmuted_grade", None) is not None
        or getattr(row, "pre_exam_weighted", None) is not None
    )
    if has_class_record:
        return evaluate_record(
            getattr(row, "transmuted_grade", None),
            coursework_standing(row),
            passing_mark,
        )
    return evaluate(
        getattr(row, "current_pace", 0),
        getattr(row, "expected_pace", 0),
        getattr(row, "total_paces", 0),
    )


# ── The year ─────────────────────────────────────────────────────────────────
# What the system is actually asked to answer: will this learner finish the
# SUBJECT below the line, where the final grade is the mean of the terms.
#
# The rule below replaces "three or more absences", which the class records
# no longer carry. It was chosen by measuring, not by taste. On the 327
# complete learner-subject-years in the school's own export it reaches
# recall 0.887 and precision 0.904 against a final average below 80.
#
# It rests on one fact from those records: learners barely move between
# terms. The median change from the first term to the rest is +1.0 point,
# only 10.1% gain more than five points, and 0.6% gain more than ten. A
# learner's first term is very nearly their year.
TERMS_PER_YEAR = 3


def required_average(
    term_grades: Sequence[float],
    line: float,
    terms_per_year: int = TERMS_PER_YEAR,
) -> float | None:
    """What the learner must average over the terms still to come.

    Returns None when no term is finished yet, or when the year is over and
    there is nothing left to average.
    """
    done = [g for g in term_grades if g is not None]
    remaining = terms_per_year - len(done)
    if not done or remaining <= 0:
        return None
    return round((line * terms_per_year - sum(done)) / remaining, 2)


def year_outlook(
    term_grades: Sequence[float],
    line: float = 80.0,
    passing_mark: float = 75.0,
    terms_per_year: int = TERMS_PER_YEAR,
) -> dict:
    """The rule-based reading of where the year is heading.

    The flag condition is that the learner needs MORE than the line itself
    from the terms that remain. That is algebraically the same as their
    average so far being below the line — needing more than L from the
    remaining terms means L*T - sum > L*(T - done), which reduces to
    sum/done < L — so it holds however many terms are finished, and it does
    not have to be restated for each one. Verified against every row of the
    school's export: the two forms never disagree.

    What makes it worth stating the long way round is that the required
    average is a number a teacher can say out loud. "You need 84 across the
    next two terms" is advice; "you are at risk" is not.
    """
    done = [g for g in term_grades if g is not None]
    if not done:
        return {
            "terms_done": 0,
            "year_average": None,
            "required": None,
            "projected": None,
            "at_risk": False,
            "will_fail": False,
            "reachable": True,
        }

    average = round(sum(done) / len(done), 2)
    needed = required_average(done, line, terms_per_year)
    needed_to_pass = required_average(done, passing_mark, terms_per_year)
    remaining = terms_per_year - len(done)

    # Where the year lands if the learner carries on exactly as they are.
    # Not a prediction, just the arithmetic of standing still — which the
    # records say is close to what most learners do.
    projected = round((sum(done) + average * remaining) / terms_per_year, 2) \
        if remaining > 0 else average

    return {
        "terms_done": len(done),
        "year_average": average,
        # None once the year is complete: there is nothing left to average.
        "required": needed,
        "required_to_pass": needed_to_pass,
        "projected": projected,
        "at_risk": average < line,
        "will_fail": average < passing_mark and remaining == 0,
        # A grade above 100 cannot be earned, so a learner needing more than
        # that can no longer reach the line however well they do. Worth
        # saying plainly rather than flagging them and leaving it at that.
        "reachable": needed is None or needed <= 100.0,
        "line": line,
    }


def evaluate_year(
    row,
    term_grades: Sequence[float],
    line: float = 80.0,
    passing_mark: float = 75.0,
    terms_per_year: int = TERMS_PER_YEAR,
) -> dict:
    """Score one subject on where its YEAR is heading.

    The final grade is the mean of the terms, so the year is what the
    system was asked to judge and the year is what this returns. Until the
    first term is finished there is no year to speak of, and the learner is
    read on the coursework banked inside the term they are in — which is
    the only honest thing available that early.
    """
    outlook = year_outlook(term_grades, line, passing_mark, terms_per_year)

    if outlook["terms_done"] == 0:
        # Nothing finished yet. Fall back to where this term is going.
        reading = evaluate_row(row, passing_mark)
        reading["outlook"] = outlook
        return reading

    standing = outlook["year_average"]
    score = standing_score(standing, passing_mark)
    # A learner whose average is above the passing mark but below the line
    # is the case the system exists for, and the plain band would call them
    # only a warning. Lift them into the at-risk band so they appear on the
    # list a teacher actually works from.
    if outlook["at_risk"] and risk_level(score) == "moderate":
        score = max(score, HIGH_AT)
    if not outlook["reachable"]:
        # The line can no longer be reached however well the rest goes.
        score = 100.0

    level = risk_level(score)
    return {
        "pace_percent": 0.0,
        "paces_behind": 0,
        "risk_score": score,
        "risk_level": level,
        "status": status_for(level),
        "intervention": year_intervention(outlook, level),
        "basis": "year",
        "standing": standing,
        "outlook": outlook,
    }


def year_intervention(outlook: dict, level: str) -> str:
    """Advice with a number in it.

    "At risk" tells a teacher nothing they can act on. "Needs 84 across the
    next two terms" tells them what to say to the learner, and to the
    guardian, and what to check for at the end of the next term.
    """
    needed = outlook.get("required")
    if level == "low" or needed is None:
        return INTERVENTIONS.get(level, INTERVENTIONS["low"])
    if not outlook["reachable"]:
        return (
            f"The subject line can no longer be reached: it would take "
            f"{needed:g} across the terms that remain. Shift the conversation "
            f"to passing the subject and to what support is available."
        )
    return (
        f"Needs an average of {needed:g} across the terms that remain to "
        f"finish at {outlook['line']:g}. Agree that target with the learner "
        f"and their guardian now, while there are terms left to use."
    )


def summarise(records: Iterable[dict]) -> dict:
    """Roll several per-subject PACE rows up into one student-level view.

    The learner's risk is driven by their *worst* subject — one who is
    excellent in three subjects and failing the fourth still needs
    intervention, and averaging that away would hide them.
    """
    rows = list(records)
    if not rows:
        return {
            "pace_percent": 0.0,
            "paces_behind": 0,
            "risk_score": 0.0,
            "risk_level": "low",
            "subjects_tracked": 0,
            "lowest_standing": None,
            "average_standing": None,
            "subjects_flagged": 0,
            "weakest_subject": None,
            "required_in_weakest": None,
            "terms_done": 0,
        }

    avg_percent = round(sum(r["pace_percent"] for r in rows) / len(rows), 1)
    worst_behind = max(r["paces_behind"] for r in rows)
    worst_score = max(r["risk_score"] for r in rows)
    standings = [r["standing"] for r in rows if r.get("standing") is not None]
    flagged = sum(1 for r in rows if r["risk_level"] != "low")
    worst = max(rows, key=lambda r: r["risk_score"])

    return {
        "pace_percent": avg_percent,
        "paces_behind": worst_behind,
        "risk_score": worst_score,
        "risk_level": risk_level(worst_score),
        "subjects_tracked": len(rows),
        # The learner's weakest subject is what drives the flag, but the
        # average is what tells a teacher whether it is one subject or all
        # of them.
        "lowest_standing": min(standings) if standings else None,
        "average_standing": round(sum(standings) / len(standings), 2) if standings else None,
        "subjects_flagged": flagged,
        # Carried up from the weakest subject, because that is the one the
        # conversation will be about and the number the teacher needs.
        "weakest_subject": worst.get("subject"),
        "required_in_weakest": worst.get("required"),
        "terms_done": worst.get("terms_done", 0),
    }
