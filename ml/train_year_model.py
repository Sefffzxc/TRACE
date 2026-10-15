"""
Train the year-risk model: from a learner's FIRST-TERM class record, estimate
whether the rest of their year in that subject will average below 80.

Run (from this folder, with the ML environment):

    .venv-ml/Scripts/python.exe train_year_model.py raw/Cleaned_Class_Record_Dataset.xlsx

Reads  : the cleaned E-Class Record export (sheet "Cleaned Data"). Never
         modified, never copied. It holds learner names, so it lives in raw/,
         which git ignores.
Writes : artifacts_year/year_dataset.csv  one row per learner-subject-year,
                                          names replaced by a hashed key
         artifacts_year/results.txt       every number reported below
         artifacts_year/risk_model_v3.joblib, model_card_v3.json

Why this target
---------------
The question the school asked is "will this learner fail the subject", which
is about the year, not one term. Three facts about the data shape the answer:

* Only ONE learner-subject-year with a first term goes on to average below 75
  over the remaining terms. A pass/fail model cannot be trained or validated
  on one case, so the line is 80 — the top of the band immediately above
  failing, where an intervention still has room to work.
* The target is the average of the terms AFTER the first, not the year
  average. The year average contains the first term, so predicting it from
  the first term would be partly reading the answer back.
* No school year in the export is complete (2022-23 holds only Q3-Q4,
  2023-24 only Q1-Q2), so "the terms after the first" is whatever later terms
  were recorded — at least one, at most three.

Why these inputs
----------------
Only the first term, all three components: Written Works, Performance Tasks
and the term examination. The prediction is made the moment that term is
graded, so all three are known, and the examination carries signal the
coursework alone does not.

Percentages, not weighted scores or the transmuted grade. The export mixes two
weight templates (30/50/20 and 40/40/20), and the live system grades under
MATATAG weights and a different transmutation table. A percentage means the
same thing under all of them; a weighted score or a transmuted grade does not.
The composite is rebuilt at one fixed set of weights for the same reason.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from joblib import dump
from sklearn.ensemble import (ExtraTreesClassifier, GradientBoostingClassifier,
                              RandomForestClassifier)
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (average_precision_score, f1_score, precision_score,
                             recall_score, roc_auc_score)
from sklearn.model_selection import StratifiedGroupKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

SEED = 42
RISK_BELOW = 80
REPEATS = 10
FOLDS = 5
MIN_RECALL = 0.80          # a warning that misses most at-risk learners is not one

# The fixed weights the composite is rebuilt at: the template 906 of the 1,016
# export rows use. Fixed, so the feature means the same thing for every school.
COMPOSITE_WEIGHTS = {"ww": 0.30, "pt": 0.50, "ex": 0.20}

FEATURES = ["q1_ww_pct", "q1_pt_pct", "q1_exam_pct", "q1_composite",
            "q1_component_gap", "grade_level"]
# Tested for the report, not deployed: the school's own transmuted grade.
WITH_GRADE = FEATURES + ["q1_term_grade"]

ART = Path("artifacts_year")
ART.mkdir(exist_ok=True)

lines: list[str] = []


def say(s: str = "") -> None:
    print(s)
    lines.append(s)


def learner_key(name: str) -> str:
    """Same pseudonym scheme as prepare_dataset.py, so keys agree across files."""
    return hashlib.sha256(f"smartpace::{name.strip().upper()}".encode()).hexdigest()[:16]


def composite(ww, pt, ex):
    w = COMPOSITE_WEIGHTS
    return w["ww"] * ww + w["pt"] * pt + w["ex"] * ex


# ══════════════════════════════════════════════════════════════════════════════
# Dataset
# ══════════════════════════════════════════════════════════════════════════════
def build(src: Path) -> pd.DataFrame:
    raw = pd.read_excel(src, sheet_name="Cleaned Data")
    raw["learner_key"] = raw["Name"].astype(str).map(learner_key)
    raw = raw.drop(columns=["Name", "Sex", "ID"])  # names leave here, sex is not used

    key = ["learner_key", "Subject", "School Year", "Grade Level"]
    q1 = raw[raw["Quarter"] == 1].set_index(key)
    later = raw[raw["Quarter"] > 1].groupby(key).agg(
        later_avg=("Term Grade", "mean"), later_terms=("Quarter", "nunique"))
    df = q1.join(later, how="inner").reset_index()

    out = pd.DataFrame({
        "learner_key": df["learner_key"],
        "subject": df["Subject"],
        "school_year": df["School Year"],
        "grade_level": df["Grade Level"].astype(float),
        "q1_ww_pct": df["Written Works Percentage Score"],
        "q1_pt_pct": df["Performance Task Percentage Score"],
        "q1_exam_pct": df["Term Exam Percentage Score"],
        "q1_term_grade": df["Term Grade"].astype(float),
        "later_terms": df["later_terms"],
        "later_avg": df["later_avg"].round(2),
    })
    out["q1_composite"] = composite(out.q1_ww_pct, out.q1_pt_pct, out.q1_exam_pct).round(2)
    out["q1_component_gap"] = (
        out[["q1_ww_pct", "q1_pt_pct", "q1_exam_pct"]].max(axis=1)
        - out[["q1_ww_pct", "q1_pt_pct", "q1_exam_pct"]].min(axis=1)
    ).round(2)
    out["at_risk"] = (out.later_avg < RISK_BELOW).astype(int)
    return out


# ══════════════════════════════════════════════════════════════════════════════
# Models and thresholds
# ══════════════════════════════════════════════════════════════════════════════
def candidates() -> dict:
    return {
        "Logistic Regression": lambda: make_pipeline(
            StandardScaler(), LogisticRegression(max_iter=2000, class_weight="balanced")),
        "Random Forest": lambda: RandomForestClassifier(
            n_estimators=400, min_samples_leaf=3, class_weight="balanced_subsample",
            random_state=SEED, n_jobs=-1),
        "Extra Trees": lambda: ExtraTreesClassifier(
            n_estimators=400, min_samples_leaf=3, class_weight="balanced",
            random_state=SEED, n_jobs=-1),
        "Gradient Boosting": lambda: GradientBoostingClassifier(
            n_estimators=150, max_depth=2, learning_rate=0.05, random_state=SEED),
    }


def pick_threshold(y, scores, higher_is_risk=True) -> float:
    """Best F1 among thresholds that keep recall >= MIN_RECALL; failing that,
    the threshold with the highest recall. Chosen on training data only."""
    s = np.asarray(scores) if higher_is_risk else -np.asarray(scores)
    best, best_t = (-1.0, -1.0), None
    for t in np.unique(s):
        pred = s >= t
        rec = recall_score(y, pred, zero_division=0)
        f1 = f1_score(y, pred, zero_division=0)
        key = (1.0 if rec >= MIN_RECALL else 0.0, f1 if rec >= MIN_RECALL else rec)
        if key > best:
            best, best_t = key, t
    return float(best_t if higher_is_risk else -best_t)


def oof_scores(make, X, y, groups, seed) -> np.ndarray:
    """Out-of-fold probabilities on the training data, for threshold choice."""
    out = np.zeros(len(y))
    cv = StratifiedGroupKFold(n_splits=4, shuffle=True, random_state=seed)
    for tr, te in cv.split(X, y, groups):
        m = make().fit(X.iloc[tr], y[tr])
        out[te] = m.predict_proba(X.iloc[te])[:, 1]
    return out


def metrics(y, pred, score=None) -> dict:
    out = {
        "recall": recall_score(y, pred, zero_division=0),
        "precision": precision_score(y, pred, zero_division=0),
        "f1": f1_score(y, pred, zero_division=0),
        "macro_f1": f1_score(y, pred, average="macro", zero_division=0),
        "false_neg": int(((pred == 0) & (y == 1)).sum()),
        "false_pos": int(((pred == 1) & (y == 0)).sum()),
    }
    if score is not None and len(np.unique(y)) > 1:
        out["roc_auc"] = roc_auc_score(y, score)
        out["pr_auc"] = average_precision_score(y, score)
    return out


def evaluate_split(df, tr, te, feats, seed) -> dict:
    X, y, g = df[feats], df["at_risk"].to_numpy(), df["learner_key"].to_numpy()
    res = {}
    # Baselines. "Q1 grade < 80" needs no fitting at all; the composite cut is
    # fitted on the training rows exactly as a model's threshold is.
    yte = y[te]
    grade = df["q1_term_grade"].to_numpy()
    res["Rule: Q1 term grade < 80"] = metrics(yte, (grade[te] < 80).astype(int), -grade[te])
    comp = df["q1_composite"].to_numpy()
    cut = pick_threshold(y[tr], comp[tr], higher_is_risk=False)
    res["Rule: Q1 composite < fitted cut"] = metrics(yte, (comp[te] <= cut).astype(int), -comp[te])
    for name, make in candidates().items():
        t = pick_threshold(y[tr], oof_scores(make, X.iloc[tr], y[tr], g[tr], seed))
        m = make().fit(X.iloc[tr], y[tr])
        p = m.predict_proba(X.iloc[te])[:, 1]
        res[name] = metrics(yte, (p >= t).astype(int), p)
    return res


def summarise(runs: list[dict]) -> pd.DataFrame:
    rows = []
    for name in runs[0]:
        vals = pd.DataFrame([r[name] for r in runs])
        row = {"model": name}
        for col in vals.columns:
            row[col] = round(vals[col].mean(), 4)
            if col in ("recall", "precision", "macro_f1"):
                row[col + "_sd"] = round(vals[col].std(), 4)
        rows.append(row)
    return pd.DataFrame(rows).sort_values("macro_f1", ascending=False)


# ══════════════════════════════════════════════════════════════════════════════
def main(src: Path) -> None:
    df = build(src)
    df.drop(columns=[]).to_csv(ART / "year_dataset.csv", index=False)
    y = df["at_risk"].to_numpy()
    groups = df["learner_key"].to_numpy()

    say("YEAR-RISK MODEL — first term in, rest of the year out")
    say("=" * 64)
    say(f"Rows (learner-subject-years with a Q1 and at least one later term): {len(df)}")
    say(f"Learners: {df.learner_key.nunique()}")
    say(f"At risk (later terms average < {RISK_BELOW}): {y.sum()} ({y.mean():.1%})")
    say(f"Later terms average < 75 (failing): {(df.later_avg < 75).sum()}")
    say(f"By school year: {df.groupby('school_year').at_risk.agg(['count', 'sum']).to_dict('index')}")
    say(f"By subject: {df.subject.value_counts().to_dict()}")
    say(f"Grade level: {df.grade_level.value_counts().to_dict()}")
    say(f"Features: {FEATURES}")

    results = {}
    for label, feats in (("deployed features (percentages)", FEATURES),
                         ("+ Q1 transmuted term grade", WITH_GRADE)):
        runs = []
        for r in range(REPEATS):
            cv = StratifiedGroupKFold(n_splits=FOLDS, shuffle=True, random_state=SEED + r)
            for tr, te in cv.split(df, y, groups):
                runs.append(evaluate_split(df, tr, te, feats, SEED + r))
        table = summarise(runs)
        results[label] = table
        say("")
        say(f"Grouped CV, {REPEATS} x {FOLDS}-fold by learner — {label}")
        say(table.to_string(index=False))

    # ── Temporal: fit on the earlier cohort, test on the later one ─────────────
    tr = np.where(df.school_year == "2023-2024")[0]
    te = np.where(df.school_year == "2025-2026")[0]
    temporal = evaluate_split(df, tr, te, FEATURES, SEED)
    say("")
    say(f"Temporal check: train 2023-2024 ({len(tr)} rows, {y[tr].sum()} at risk) "
        f"-> test 2025-2026 ({len(te)} rows, {y[te].sum()} at risk)")
    say(pd.DataFrame(temporal).T.round(4).to_string())

    # ── The deployed model ─────────────────────────────────────────────────────
    table = results["deployed features (percentages)"]
    models_only = table[~table.model.str.startswith("Rule")]
    best = models_only.iloc[0]["model"]
    make = candidates()[best]
    X = df[FEATURES]
    threshold = pick_threshold(y, oof_scores(make, X, y, groups, SEED))
    model = make().fit(X, y)

    est = model[-1] if hasattr(model, "steps") else model
    if hasattr(est, "feature_importances_"):
        importances = np.asarray(est.feature_importances_)
    else:
        importances = np.abs(est.coef_[0])
        importances = importances / importances.sum()

    say("")
    say(f"Deployed: {best}, threshold {threshold:.3f}, fitted on all {len(df)} rows")
    say("Importances: " + ", ".join(
        f"{f}={v:.3f}" for f, v in sorted(zip(FEATURES, importances), key=lambda kv: -kv[1])))

    friendly = {
        "q1_ww_pct": "first-term Written Works",
        "q1_pt_pct": "first-term Performance Tasks",
        "q1_exam_pct": "first-term examination",
        "q1_composite": "first-term overall standing",
        "q1_component_gap": "uneven first-term components",
        "grade_level": "grade level",
    }
    interventions = {
        "first-term Written Works":
            "Written Works were the weak point in the first term. Check quiz and "
            "seatwork results with the learner and plan short practice before the "
            "next term's written works.",
        "first-term Performance Tasks":
            "Performance Tasks pulled the first term down. Look for missing or "
            "incomplete outputs and agree on a plan to submit the next ones on time.",
        "first-term examination":
            "The first-term examination was much weaker than the coursework. Review "
            "how the learner prepares for tests and revisit the topics they missed.",
        "first-term overall standing":
            "The whole first term sits low. Meet the learner (and the adviser) now: "
            "the remaining terms tend to follow the first closely.",
        "uneven first-term components":
            "The first-term components are very uneven. Find out what held back "
            "the weakest one — absence, a missing output, or a topic not understood.",
        "grade level":
            "Review the learner's first-term record with the adviser.",
    }
    bundle = {
        "model": model,
        "features": FEATURES,
        "threshold": threshold,
        "importances": importances,
        "medians": {f: float(df[f].median()) for f in FEATURES},
        "feature_means": {f: float(df[f].mean()) for f in FEATURES},
        "feature_stds": {f: float(df[f].std() or 1.0) for f in FEATURES},
        "friendly_names": friendly,
        "interventions": interventions,
        "composite_weights": COMPOSITE_WEIGHTS,
    }
    dump(bundle, ART / "risk_model_v3.joblib")

    def records(t: pd.DataFrame) -> list:
        return json.loads(t.to_json(orient="records"))

    card = {
        "model": type(est).__name__,
        "version": "3.0.0-year",
        "task": (f"binary classification: will the learner's remaining terms in a "
                 f"subject average below {RISK_BELOW}, predicted from the first "
                 f"term's class record once that term is graded"),
        "dataset": {
            "source": "Cleaned E-Class Record export, Looc National High School, "
                      "SY 2023-2024 and 2025-2026 (2022-2023 has no first term)",
            "rows_used": int(len(df)),
            "learners": int(df.learner_key.nunique()),
            "at_risk": int(y.sum()),
            "at_risk_rate": round(float(y.mean()), 4),
            "later_terms_below_75": int((df.later_avg < 75).sum()),
            "subjects": df.subject.value_counts().to_dict(),
            "grade_levels": {str(int(k)): int(v) for k, v in df.grade_level.value_counts().items()},
            "balancing_applied": "class weights only; no resampling",
        },
        "features_used": FEATURES,
        "features_explained": {
            "q1_ww_pct": "first-term Written Works percentage score",
            "q1_pt_pct": "first-term Performance Tasks percentage score",
            "q1_exam_pct": "first-term examination percentage score",
            "q1_composite": "0.30 x WW% + 0.50 x PT% + 0.20 x Exam%, at fixed weights",
            "q1_component_gap": "highest minus lowest of the three percentages",
            "grade_level": "7-10",
        },
        "features_excluded": {
            "Weighted scores, initial grade, transmuted term grade":
                "depend on the weight template and transmutation table, which differ "
                "between the training export and the live MATATAG record; the "
                "percentages carry the same information in a form that means the "
                "same thing under every template",
            "Later terms": "they are the outcome",
            "Name": "hashed to a learner key for grouping, never stored",
            "Sex": "excluded as a fairness liability",
            "Subject / Section / School Year": "identifiers; 95% of rows are Filipino",
        },
        "target": f"average term grade of the terms after the first < {RISK_BELOW}",
        "protocol": {
            "splits": f"StratifiedGroupKFold by learner, {FOLDS} folds x {REPEATS} repeats",
            "threshold_selection": f"inner 4-fold grouped CV on training learners only; "
                                   f"best F1 subject to recall >= {MIN_RECALL}",
            "baselines": "Q1 term grade < 80 (no fitting); Q1 composite below a cut "
                         "fitted on the training folds",
        },
        "threshold": threshold,
        "results": records(results["deployed features (percentages)"]),
        "results_with_term_grade": records(results["+ Q1 transmuted term grade"]),
        "temporal_validation": {
            "train": "2023-2024", "test": "2025-2026",
            "results": {k: {m: round(float(v), 4) for m, v in d.items()}
                        for k, d in temporal.items()},
        },
        "limitations": [
            f"Predicts below {RISK_BELOW}, not failing (below 75): only "
            f"{int((df.later_avg < 75).sum())} learner-subject-year in the export "
            "went on to average below 75, too few to train or validate a pass/fail model.",
            f"Trained on {len(df)} learner-subject-years with {int(y.sum())} at risk. "
            "Every metric carries a wide margin; read the standard deviations.",
            "No school year in the export is complete, so 'the remaining terms' "
            "means whichever later terms were recorded (one to three).",
            "95% of rows are Filipino and most are Grade 8. Conclusions do not "
            "extend to other subjects or grade levels without new data.",
            "Learners' grades barely move between terms, so the first term's own "
            "grade is a strong rule on its own. The comparison table reports it "
            "beside the model; where the model does not beat it, that is the finding.",
            "The export carries no attendance column, so attendance plays no part.",
        ],
    }
    (ART / "model_card_v3.json").write_text(json.dumps(card, indent=2))
    (ART / "results.txt").write_text("\n".join(lines))


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "raw/Cleaned_Class_Record_Dataset.xlsx"))
