"""DepEd class-record arithmetic (MATATAG three-term E-Class Record).

One module, used by the save path, by the feature builder the model reads and
by the responses the class-record screen renders, so that a grade shown on
screen, a grade stored in the database and a grade the model was given can
never disagree.

It follows the UPDATED Grades 2-10 three-term E-Class Record exactly:

    Component total   = sum of the item scores recorded
    Component PS      = Total / Highest Possible Score x 100
    Component WS      = PS x the component's weight

    Examinations are different. Each of the two summative tests and the term
    examination carries its own share of the component — 30, 30 and 40 — so
    each is converted to its own weighted figure and the three are added to
    make the component's PS, which is then weighted like the other two.

    Initial Grade     = WS(Written Works) + WS(Performance Tasks) + WS(Exams)
    Term Grade        = Initial Grade, through the transmutation table
    Descriptor        = the band the term grade falls in

Two weight profiles, because the template ships as two different workbooks:

    CORE   Science, Mathematics, English, Filipino, Araling Panlipunan
           Written Works 20, Performance Tasks 50, Examinations 30
    MAPEH  Music and Arts; Physical Education and Health
           Written Works 20, Performance Tasks 60, Examinations 20

The transmutation table is reproduced as data rather than as a formula. The
MATATAG bands are irregular — 1.18 apart above an initial grade of 70 and
about 4.67 apart below it, with boundaries published to two decimal places —
and a formula fitted to them lands on the wrong side of an edge. The table is
what the workbook uses, so the table is what is used here.
"""
from __future__ import annotations

from typing import Optional, Sequence

# A blank cell is not a zero. A learner who has not sat an assessment has no
# score for it; a learner who sat it and scored nothing has a 0. The two are
# stored differently and must stay different all the way through.
Score = Optional[float]

CORE = "core"
MAPEH = "mapeh"

TEMPLATES = {
    CORE: {
        "short": "Core subject",
        "label": "Science · Mathematics · English · Filipino · Araling Panlipunan",
        "ww_weight": 20.0,
        "pt_weight": 50.0,
        "ex_weight": 30.0,
    },
    MAPEH: {
        "short": "MAPEH",
        "label": "Music and Arts · Physical Education and Health",
        "ww_weight": 20.0,
        "pt_weight": 60.0,
        "ex_weight": 20.0,
    },
}

# ST1, ST2, Term Examination — their shares of the examinations component.
EXAM_SUBWEIGHTS = (30.0, 30.0, 40.0)
EXAM_LABELS = ("ST1", "ST2", "TE")

# Three terms, not four quarters.
TERMS = (1, 2, 3)
TERM_LABELS = {1: "First Term", 2: "Second Term", 3: "Third Term"}

# (minimum initial grade, transmuted grade). Ordered high to low; the first
# row whose minimum the initial grade reaches is the one that applies.
TRANSMUTATION = (
    (99.50, 100), (98.32, 99), (97.14, 98), (95.96, 97), (94.78, 96),
    (93.60, 95), (92.42, 94), (91.24, 93), (90.06, 92), (88.88, 91),
    (87.70, 90), (86.52, 89), (85.34, 88), (84.16, 87), (82.98, 86),
    (81.80, 85), (80.62, 84), (79.44, 83), (78.26, 82), (77.08, 81),
    (75.90, 80), (74.72, 79), (73.54, 78), (72.36, 77), (71.18, 76),
    (70.00, 75), (65.34, 74), (60.67, 73), (56.01, 72), (51.34, 71),
    (46.67, 70), (42.01, 69), (37.34, 68), (32.68, 67), (28.01, 66),
    (23.35, 65), (18.68, 64), (14.01, 63), (9.35, 62), (4.68, 61),
    (0.00, 60),
)

# MATATAG descriptors. The passing mark is 75; 75-79 ("Connecting") is the
# band SmartPACE treats as at risk, because a learner sitting there is the
# one an early warning exists for.
DESCRIPTORS = (
    (90, "Advancing"),
    (80, "Benchmarking"),
    (75, "Connecting"),
    (65, "Developing"),
    (0, "Emerging"),
)


def _recorded(scores: Optional[Sequence[Score]]) -> list[float]:
    if not scores:
        return []
    return [float(s) for s in scores if s is not None]


def component_total(scores: Optional[Sequence[Score]]) -> Optional[float]:
    """Raw total of the recorded items, or None when nothing is recorded."""
    got = _recorded(scores)
    return round(sum(got), 2) if got else None


def percentage_score(
    scores: Optional[Sequence[Score]],
    highest: Optional[Sequence[Score]],
) -> Optional[float]:
    """Total over the component's full highest possible score, as a percentage.

    The denominator is every item in the HIGHEST POSSIBLE SCORE row, not only
    the items the learner has a mark for. That is what the workbook does, and
    it is what the records the model was trained on were computed with, so
    departing from it here would put the screen, the stored grade and the
    model's input on three different scales.

    The consequence is worth stating plainly: part-way through a term, before
    every assessment has been given, this reads low for everybody. That is
    why ``complete`` travels alongside it.

    Not capped at 100 — the school's own records contain learners who scored
    above the highest possible mark on a component, and quietly rewriting
    those would put this sheet out of step with the teacher's.
    """
    total = component_total(scores)
    cap = sum(float(h) for h in (highest or []) if h)
    if total is None or cap <= 0:
        return None
    return round(total / cap * 100, 2)


def weighted_score(percentage: Optional[float], weight: float) -> Optional[float]:
    if percentage is None:
        return None
    return round(percentage * weight / 100.0, 2)


def exam_component(
    scores: Optional[Sequence[Score]],
    highest: Optional[Sequence[Score]],
    subweights: Sequence[float] = EXAM_SUBWEIGHTS,
) -> dict:
    """The examinations component: ST1, ST2 and the term examination.

    Each sits on its own scale and carries its own share, so each is
    converted separately and the three are added. The per-examination figures
    are returned as well because the workbook prints them as their own
    columns (WS ST1, WS ST2, WS TE).
    """
    scores = list(scores or [])
    highest = list(highest or [])
    parts: list[Optional[float]] = []
    for index, share in enumerate(subweights):
        score = scores[index] if index < len(scores) else None
        cap = highest[index] if index < len(highest) else None
        if score is None or not cap:
            parts.append(None)
        else:
            parts.append(round(float(score) / float(cap) * share, 2))

    taken = [p for p in parts if p is not None]
    return {
        "parts": parts,
        # Blank examinations contribute nothing, exactly as the workbook's
        # SUM over the three weighted cells does.
        "percentage": round(sum(taken), 2) if taken else None,
        "complete": all(p is not None for p in parts),
    }


def transmute(initial_grade: Optional[float]) -> Optional[int]:
    """Initial grade to term grade, through the published table."""
    if initial_grade is None:
        return None
    value = float(initial_grade)
    for minimum, grade in TRANSMUTATION:
        if value >= minimum:
            return grade
    return TRANSMUTATION[-1][1]


def descriptor(transmuted: Optional[int]) -> Optional[str]:
    if transmuted is None:
        return None
    for floor, text in DESCRIPTORS:
        if transmuted >= floor:
            return text
    return None


def compute(
    *,
    ww_scores: Optional[Sequence[Score]] = None,
    pt_scores: Optional[Sequence[Score]] = None,
    exam_scores: Optional[Sequence[Score]] = None,
    ww_highest: Optional[Sequence[Score]] = None,
    pt_highest: Optional[Sequence[Score]] = None,
    exam_highest: Optional[Sequence[Score]] = None,
    template: str = CORE,
    ww_weight: Optional[float] = None,
    pt_weight: Optional[float] = None,
    ex_weight: Optional[float] = None,
    exam_subweights: Sequence[float] = EXAM_SUBWEIGHTS,
) -> dict:
    """Every derived figure for one learner, one subject, one term.

    The weights default to the template's, and are only overridden when a
    record carries its own.
    """
    profile = TEMPLATES.get(template, TEMPLATES[CORE])
    w_ww = profile["ww_weight"] if ww_weight is None else ww_weight
    w_pt = profile["pt_weight"] if pt_weight is None else pt_weight
    w_ex = profile["ex_weight"] if ex_weight is None else ex_weight

    ww_pct = percentage_score(ww_scores, ww_highest)
    pt_pct = percentage_score(pt_scores, pt_highest)
    exam = exam_component(exam_scores, exam_highest, exam_subweights)

    ww_ws = weighted_score(ww_pct, w_ww)
    pt_ws = weighted_score(pt_pct, w_pt)
    ex_ws = weighted_score(exam["percentage"], w_ex)

    # The workbook computes an initial grade as soon as ANY component has a
    # mark, summing the others as zero. That is the defect that made 65 rows
    # of the school's own export read as failing learners when they were
    # really empty columns, so this requires all three. An incomplete record
    # shows no grade rather than a wrong one.
    parts = [ww_ws, pt_ws, ex_ws]
    complete = all(p is not None for p in parts) and exam["complete"]
    initial = round(sum(parts), 2) if all(p is not None for p in parts) else None
    final = transmute(initial)

    return {
        "template": template,
        "ww_total_raw": component_total(ww_scores),
        "ww_percent": ww_pct,
        "ww_weighted": ww_ws,
        "pt_total_raw": component_total(pt_scores),
        "pt_percent": pt_pct,
        "pt_weighted": pt_ws,
        # The three per-examination weighted figures the workbook prints.
        "exam_parts": exam["parts"],
        "exam_percent": exam["percentage"],
        "exam_weighted": ex_ws,
        "initial_grade": initial,
        "transmuted_grade": final,
        "descriptor": descriptor(final),
        "complete": complete,
        # What the model is shown: coursework banked before the examinations.
        # The examinations are the event the warning is meant to precede.
        "pre_exam_weighted": (round(ww_ws + pt_ws, 2)
                              if ww_ws is not None and pt_ws is not None else None),
        # The most that coursework could be worth. Stored with the figure
        # above so a learner's standing can be put on a 0-100 axis later
        # without having to go back and ask which weights applied.
        "pre_exam_max": round(w_ww + w_pt, 2),
    }


def missing_items(scores: Optional[Sequence[Score]]) -> int:
    if not scores:
        return 0
    return sum(1 for s in scores if s is None)


__all__ = [
    "CORE", "MAPEH", "TEMPLATES", "EXAM_SUBWEIGHTS", "EXAM_LABELS",
    "TERMS", "TERM_LABELS", "TRANSMUTATION", "DESCRIPTORS",
    "compute", "transmute", "descriptor", "exam_component",
    "component_total", "percentage_score", "weighted_score", "missing_items",
]