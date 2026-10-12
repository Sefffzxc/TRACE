"""
Build the modelling dataset from the merged DepEd E-Class Record export.

Run:  python prepare_dataset.py

Reads  : Merged_Class_Record_Dataset.xlsx  (the school's file, never modified)
Writes : classrecord_modelling.csv         (derived, no personal information)
         prepare_report.txt                (what was dropped and why)

Three rules govern this file and none of them may be relaxed quietly:

1. Learner names never leave this script. The name is hashed into a stable
   learner key so that cross-validation can keep a learner's rows together,
   and the name column itself is dropped before anything is written.

2. Nothing is invented, resampled or rebalanced here. Rows are only removed,
   and every removal is counted in the report.

3. Only values a teacher has actually recorded BEFORE the term examination
   become features. The term examination, the initial grade and the term
   grade are outcomes, not inputs.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pandas as pd

SRC = Path("Merged_Class_Record_Dataset.xlsx")
OUT = Path("classrecord_modelling.csv")
REPORT = Path("prepare_report.txt")

# The line the school asked the system to flag. 75 is the DepEd passing mark;
# 75-79 is "Fairly Satisfactory", the band immediately above failing. Flagging
# at 80 is what makes this an early warning rather than a post-mortem.
RISK_BELOW = 80

WW_T, WW_P, WW_W = ("Written Works Total",
                    "Written Works Percentage Score",
                    "Written Works Weighted Score")
PT_T, PT_P, PT_W = ("Performance Task Total",
                    "Performance Task Percentage Score",
                    "Performance Task Weighted Score")
TERM_GRADE = "Term Grade"


def learner_key(name: str) -> str:
    """A stable pseudonym. Same learner, same key, across every run; the name
    itself cannot be recovered from it."""
    return hashlib.sha256(f"smartpace::{name.strip().upper()}".encode()).hexdigest()[:16]


def main() -> None:
    raw = pd.read_excel(SRC, sheet_name="Dataset")
    log: list[str] = [f"Source rows: {len(raw)}"]

    df = raw.copy()

    # ── 1. Rows where the term is not finished ────────────────────────────
    # No term grade means no outcome to learn from. These are not failures.
    unfinished = df[TERM_GRADE].isna()
    log.append(f"Dropped, term not yet graded (no Term Grade): {unfinished.sum()}")
    df = df[~unfinished]

    # ── 2. Rows graded with a component left blank ────────────────────────
    # The class-record formula treats an empty Written Works or Performance
    # Task column as zero and still produces a grade. The school's own notes
    # flag these. They record what a teacher had entered by the time the file
    # was saved, not how the learner performed, and most are whole classes at
    # once. Keeping them would teach the model that an empty column means a
    # failing learner.
    blank = df[WW_T].isna() | df[PT_T].isna()
    log.append(f"Dropped, graded with a blank component: {blank.sum()}")
    log.append(f"  of those, Term Grade < {RISK_BELOW}: "
               f"{(df.loc[blank, TERM_GRADE] < RISK_BELOW).sum()}")
    log.append("  affected class records: " + "; ".join(
        f"{s} {y} Q{q} ({n})" for (s, y, q), n in
        df[blank].groupby(["Section", "School Year", "Quarter"]).size().items()))
    df = df[~blank]

    # ── 3. Features ───────────────────────────────────────────────────────
    out = pd.DataFrame({
        "learner_key": df["Name"].map(learner_key),
        "school_year": df["School Year"],
        "quarter": df["Quarter"].astype(int),
        "grade_level": df["Grade Level"].astype(int),
        "ww_total": df[WW_T].astype(float),
        "ww_pct": df[WW_P].astype(float),
        "ww_weighted": df[WW_W].astype(float),
        "pt_total": df[PT_T].astype(float),
        "pt_pct": df[PT_P].astype(float),
        "pt_weighted": df[PT_W].astype(float),
    })
    # What the learner has banked before sitting the term examination. This is
    # the single most informative number available and the arithmetic baseline
    # is built on it, so it is named rather than left for the model to infer.
    out["pre_exam_weighted"] = out["ww_weighted"] + out["pt_weighted"]
    # How far the two recorded components sit apart. A learner carried by one
    # component is in a different position from one who is even across both.
    out["component_gap"] = (out["ww_pct"] - out["pt_pct"]).abs()

    out["term_grade"] = df[TERM_GRADE].astype(float)
    out["at_risk"] = (out["term_grade"] < RISK_BELOW).astype(int)

    log.append(f"\nRows kept: {len(out)}")
    log.append(f"Distinct learners: {out['learner_key'].nunique()}")
    log.append(f"Learners appearing more than once: "
               f"{(out.groupby('learner_key').size() > 1).sum()}")
    log.append(f"At risk (term grade < {RISK_BELOW}): {int(out['at_risk'].sum())} "
               f"({100 * out['at_risk'].mean():.2f}%)")
    log.append(f"Of those, below the passing mark of 75: "
               f"{int((out['term_grade'] < 75).sum())}")
    log.append("\nNothing was oversampled, synthesised or rebalanced. "
               "Class imbalance is handled at training time by class weighting.")
    log.append("\nRows by school year:\n" +
               out["school_year"].value_counts().sort_index().to_string())
    log.append("\nRisk rate by school year:\n" +
               out.groupby("school_year")["at_risk"].mean().round(4).to_string())

    out.to_csv(OUT, index=False)
    REPORT.write_text("\n".join(log) + "\n")
    print("\n".join(log))
    print(f"\nwrote {OUT} ({len(out)} rows, {out.shape[1]} columns)")


if __name__ == "__main__":
    main()
