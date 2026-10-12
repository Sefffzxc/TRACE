"""Package the trained model for the inference service.

Run after train_risk_model.py:  python finalize_bundle.py

The service needs more than the fitted estimator. It explains each flag by
asking which feature sits furthest from the cohort's own average, weighted by
how much the model relies on that feature, so it needs the training means and
spreads. It also needs a readable name for every feature and something useful
to say to the teacher who sees the flag.

Writes artifacts/risk_model.joblib in place, with every key the service reads.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from joblib import dump, load

ART = Path("artifacts")

FRIENDLY = {
    "ww_total":          "Written Works total",
    "ww_pct":            "Written Works percentage",
    "ww_weighted":       "Written Works weighted score",
    "pt_total":          "Performance Task total",
    "pt_pct":            "Performance Task percentage",
    "pt_weighted":       "Performance Task weighted score",
    "pre_exam_weighted": "coursework banked before the term exam",
    "component_gap":     "gap between the two components",
    "grade_level":       "grade level",
    "quarter":           "quarter",
}

# What a teacher can actually do about it. Written in the second person and
# kept specific: "monitor the learner" is not an intervention.
INTERVENTIONS = {
    "Written Works total":
        "Check which written works are missing or unscored and arrange for the "
        "learner to complete them before the term examination.",
    "Written Works percentage":
        "Written work is the weak component. Review the last few quizzes with "
        "the learner and identify which competencies are not holding.",
    "Written Works weighted score":
        "Written work is pulling the grade down. Work out how much the learner "
        "needs on the remaining written tasks and tell them the number.",
    "Performance Task total":
        "Performance tasks are missing or incomplete. Agree a submission date "
        "for each outstanding task and record it.",
    "Performance Task percentage":
        "Performance tasks are the weak component, and they carry the largest "
        "share of the grade. Re-teach the task criteria and allow a revision.",
    "Performance Task weighted score":
        "Performance tasks carry the heaviest weight, so this is where the "
        "grade is being lost and where recovery is fastest.",
    "coursework banked before the term exam":
        "Coursework so far is below the level that usually ends the term under "
        "80. Work out the term-examination mark needed to reach 80 and discuss "
        "it with the learner and their guardian now, not after the exam.",
    "gap between the two components":
        "The learner is far stronger in one component than the other. Find out "
        "which and why — an uneven profile often means missed tasks rather "
        "than missed understanding.",
    "grade level": "Review alongside the learner's other subjects.",
    "quarter": "Compare with the learner's earlier quarters in this subject.",
}


def main() -> None:
    bundle = load(ART / "risk_model.joblib")
    features = bundle["features"]
    df = pd.read_csv("classrecord_modelling.csv")
    X = df[features].astype(float)

    # Imputed the same way training did, so the statistics the explanation is
    # measured against describe the rows the model actually saw.
    medians = X.median()
    Xi = X.fillna(medians)

    importances = bundle["model"].feature_importances_
    bundle.update({
        "medians": {f: float(medians[f]) for f in features},
        "feature_means": {f: float(Xi[f].mean()) for f in features},
        "feature_stds": {f: float(Xi[f].std(ddof=0)) or 1.0 for f in features},
        # A sequence aligned to `features`, because the service zips the two.
        "importances": [float(v) for v in importances],
        "friendly_names": FRIENDLY,
        "interventions": INTERVENTIONS,
    })

    missing = [f for f in features if f not in FRIENDLY]
    if missing:
        raise SystemExit(f"no readable name for: {missing}")

    dump(bundle, ART / "risk_model.joblib")
    print("bundle keys:", sorted(bundle))
    print("features   :", features)
    print("threshold  :", round(bundle['threshold'], 3))
    print("medians    :", {k: round(v, 2) for k, v in bundle['medians'].items()})


if __name__ == "__main__":
    main()
