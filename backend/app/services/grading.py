"""DepEd class-record arithmetic.

One module, used by the save path, by the feature builder the model reads and
by the responses the class-record screen renders, so that a grade shown on
screen, a grade stored in the database and a grade the model was given can
never disagree.

Everything here follows DepEd Order 8, s. 2015:

    Total        = sum of the item scores actually recorded
    PS           = Total / Highest Possible Score x 100
    WS           = PS x the component's weight
    Initial      = WS(Written Works) + WS(Performance Tasks) + WS(Term Exam)
    Transmuted   = Initial, mapped through the transmutation table

``transmute`` was checked against all 1,159 graded rows of the merged
E-Class-Record export from Looc National High School and reproduces every
one of them exactly. The integer arithmetic inside it is not decoration: the
band edges fall on values such as 69.6 and 80.8 that binary floating point
represents slightly low, and computing them in floats puts nine of those
1,159 rows one grade point too low.
"""
from __future__ import annotations

from typing import Iterable, Optional, Sequence

# A blank cell is not a zero. A learner who has not yet sat an assessment has
# no score for it; a learner who sat it and scored nothing has a 0. The two
# are stored differently and must stay different all the way through.
Score = Optional[float]


def _recorded(scores: Optional[Sequence[Score]]) -> list[float]:
    """The scores a teacher has actually entered, blanks discarded."""
    if not scores:
        return []
    return [float(s) for s in scores if s is not None]


def component_total(scores: Optional[Sequence[Score]]) -> Optional[float]:
    """Raw total of the recorded items, or None when nothing is recorded."""
    got = _recorded(scores)
    return round(sum(got), 2) if got else None


def highest_possible(
    scores: Optional[Sequence[Score]],
    highest: Optional[Sequence[Score]],
) -> Optional[float]:
    """The denominator for the percentage score.

    Only the items the learner has a score for count toward it. Dividing by
    the whole term's possible marks while half the column is still empty
    would report every learner as failing in the first week of the term.
    """
    if not scores or not highest:
        return None
    total = 0.0
    for score, cap in zip(scores, highest):
        if score is not None and cap:
            total += float(cap)
    return round(total, 2) if total > 0 else None


def percentage_score(
    scores: Optional[Sequence[Score]],
    highest: Optional[Sequence[Score]],
) -> Optional[float]:
    """Total over highest possible, as a percentage.

    Deliberately not capped at 100: the school's own records contain learners
    who scored above the highest possible mark on a component, and a class
    record that silently rewrote those would not match the teacher's sheet.
    """
    total = component_total(scores)
    cap = highest_possible(scores, highest)
    if total is None or not cap:
        return None
    return round(total / cap * 100, 2)


def weighted_score(percentage: Optional[float], weight_percent: float) -> Optional[float]:
    """Percentage score scaled by the component's share of the grade."""
    if percentage is None:
        return None
    return round(percentage * weight_percent / 100.0, 2)


def transmute(initial_grade: Optional[float]) -> Optional[int]:
    """The DepEd transmutation table, as arithmetic rather than a lookup.

    Above an initial grade of 60 the table advances one grade point every
    1.6 marks from a base of 75; below 60 it advances one point every 4
    marks from a base of 60. Both are computed in integer hundredths so that
    a value sitting exactly on a band edge lands in the upper band, which is
    what the published table does and what the school's records show.
    """
    if initial_grade is None:
        return None
    hundredths = round(float(initial_grade) * 100)
    if hundredths >= 6000:
        return min(100, 75 + (hundredths - 6000) // 160)
    return 60 + hundredths // 400


# DepEd descriptors. The passing mark is 75; 75-79 is the band SmartPACE
# treats as at risk, because a learner sitting there is the one an early
# warning is for.
_DESCRIPTORS = (
    (90, "O", "Outstanding"),
    (85, "VS", "Very Satisfactory"),
    (80, "S", "Satisfactory"),
    (75, "FS", "Fairly Satisfactory"),
    (0, "DNME", "Did Not Meet Expectations"),
)


def letter_grade(transmuted: Optional[int]) -> Optional[str]:
    if transmuted is None:
        return None
    for floor, letter, _ in _DESCRIPTORS:
        if transmuted >= floor:
            return letter
    return None


def descriptor(transmuted: Optional[int]) -> Optional[str]:
    if transmuted is None:
        return None
    for floor, _, text in _DESCRIPTORS:
        if transmuted >= floor:
            return text
    return None


def compute(
    *,
    ww_scores: Optional[Sequence[Score]] = None,
    pt_scores: Optional[Sequence[Score]] = None,
    sa_scores: Optional[Sequence[Score]] = None,
    te_score: Score = None,
    ww_highest: Optional[Sequence[Score]] = None,
    pt_highest: Optional[Sequence[Score]] = None,
    sa_highest: Optional[Sequence[Score]] = None,
    te_highest: Score = None,
    ww_weight: float = 30.0,
    pt_weight: float = 50.0,
    te_weight: float = 20.0,
) -> dict:
    """Every derived figure for one learner, one subject, one grading period.

    The third component of the DepEd senior-high record holds the summative
    tests and the term examination together, so the two are summed into one
    column group before the percentage is taken.
    """
    exam_scores = list(sa_scores or []) + [te_score]
    exam_highest = list(sa_highest or []) + [te_highest]

    ww_pct = percentage_score(ww_scores, ww_highest)
    pt_pct = percentage_score(pt_scores, pt_highest)
    te_pct = percentage_score(exam_scores, exam_highest)

    ww_ws = weighted_score(ww_pct, ww_weight)
    pt_ws = weighted_score(pt_pct, pt_weight)
    te_ws = weighted_score(te_pct, te_weight)

    # An initial grade needs every component. Summing what happens to be
    # present treats an un-sat examination as a zero, and that is exactly the
    # defect that made 65 rows of the school's export look like failures.
    parts = [ww_ws, pt_ws, te_ws]
    initial = round(sum(parts), 2) if all(p is not None for p in parts) else None
    final = transmute(initial)

    return {
        "ww_total_raw": component_total(ww_scores),
        "ww_percent": ww_pct,
        "ww_weighted": ww_ws,
        "pt_total_raw": component_total(pt_scores),
        "pt_percent": pt_pct,
        "pt_weighted": pt_ws,
        "te_total_raw": component_total(exam_scores),
        "te_percent": te_pct,
        "te_weighted": te_ws,
        "initial_grade": initial,
        "transmuted_grade": final,
        "letter_grade": letter_grade(final),
        "descriptor": descriptor(final),
        # What the model is allowed to see. The term examination is the event
        # the warning is supposed to precede, so it is excluded here even
        # when it has already been recorded.
        "pre_exam_weighted": (round(ww_ws + pt_ws, 2)
                              if ww_ws is not None and pt_ws is not None else None),
    }


def missing_items(scores: Optional[Sequence[Score]]) -> int:
    """How many cells in a component are still blank."""
    if not scores:
        return 0
    return sum(1 for s in scores if s is None)


__all__ = [
    "compute", "transmute", "letter_grade", "descriptor",
    "component_total", "percentage_score", "weighted_score",
    "highest_possible", "missing_items",
]
