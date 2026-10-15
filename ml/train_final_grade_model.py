"""
Train the final-grade model: from a learner's FIRST-TERM class record in a
subject, predict their FINAL grade in it, with a likely range and the chance
of failing (final grade below 75).

Run (from this folder, with the ML environment):

    .venv-ml/Scripts/python.exe -I train_final_grade_model.py raw/Class_Record_Dataset_v6.xlsx

Reads  : the cleaned v6 export (sheet "Cleaned Data"). Never modified, never
         copied; it holds learner names, so it lives in raw/, which git ignores.
Writes : artifacts_final/final_dataset.csv    one row per learner-subject-year,
                                              names replaced by a hashed key
         artifacts_final/results.txt          every number reported
         artifacts_final/final_grade_model_v4.joblib, model_card_v4.json

Why regression
--------------
Only 14 of the 1,393 learner-subject-years with a first term failed. A yes/no
classifier would learn "fail" from 14 examples. Predicting the grade instead
lets every learner teach the model how grades move after the first term, and
"likely to fail" is then read off the predicted grade and its range.

Why the CHANGE from the first term
----------------------------------
The final grade is exactly the mean of the term grades (checked on every
complete year in the export), so it contains the first term. The model
predicts the change — final grade minus first-term grade — and the system adds
it to the learner's own first-term grade. Two reasons:

* The honest baseline, "the final grade will equal the first term", is a
  change of zero, so the model is measured directly against it.
* The training export was graded under older transmutation tables and the
  live system uses MATATAG's. A change is far less sensitive to the table
  than a grade is, and anchoring on the school's own first-term grade keeps
  the prediction on the scale the school actually reports.

The range and the chance of failing come from the model's own errors on
learners it was not trained on (out-of-fold residuals), not from an assumed
distribution.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from joblib import dump
from sklearn.ensemble import (ExtraTreesRegressor, GradientBoostingRegressor,
                              RandomForestRegressor)
from sklearn.linear_model import Ridge
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

SEED = 42
PASSING = 75
REPEATS = 5
FOLDS = 5
INTERVAL = 0.80            # the "likely range" covers 80% of outcomes

FEATURES = ["q1_term_grade", "q1_initial_grade", "q1_ww_pct", "q1_pt_pct",
            "q1_exam_pct", "q1_component_gap", "grade_level"]

ART = Path("artifacts_final")
ART.mkdir(exist_ok=True)
lines: list[str] = []


def say(s: str = "") -> None:
    print(s)
    lines.append(s)


def learner_key(name: str, school: str) -> str:
    """A stable pseudonym. Same learner, same key; the name cannot be
    recovered from it. The school is part of it: two schools can each have a
    learner with the same name."""
    raw = f"smartpace::{school.strip().upper()}::{name.strip().upper()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# ══════════════════════════════════════════════════════════════════════════════
# Dataset
# ══════════════════════════════════════════════════════════════════════════════
def build(src: Path) -> pd.DataFrame:
    raw = pd.read_excel(src, sheet_name="Cleaned Data")
    raw["learner_key"] = [learner_key(str(n), str(s))
                          for n, s in zip(raw["Name"], raw["School"])]
    raw = raw.drop(columns=["Name", "Sex", "ID"])  # names leave here; sex is not used

    key = ["learner_key", "School", "Subject", "School Year", "Grade Level"]
    q1 = raw[raw["Quarter"] == 1].drop_duplicates(key).set_index(key)
    terms = raw.groupby(key)["Quarter"].nunique().rename("terms_recorded")
    df = q1.join(terms).reset_index()

    pct = df[["Written Works Percentage Score", "Performance Task Percentage Score",
              "Term Exam Percentage Score"]]
    out = pd.DataFrame({
        "learner_key": df["learner_key"],
        "school": df["School"],
        "subject": df["Subject"],
        "school_year": df["School Year"],
        "grade_level": df["Grade Level"].astype(float),
        "q1_term_grade": df["Term Grade"].astype(float),
        "q1_initial_grade": df["Initial Grade"].astype(float),
        "q1_ww_pct": df["Written Works Percentage Score"],
        "q1_pt_pct": df["Performance Task Percentage Score"],
        "q1_exam_pct": df["Term Exam Percentage Score"],
        "q1_component_gap": (pct.max(axis=1) - pct.min(axis=1)).round(2),
        "terms_recorded": df["terms_recorded"],
        "final_grade": df["Final Grade"].astype(float),
        "failed": (df["Remarks"].str.strip().str.lower() == "failed").astype(int),
    })
    out["change"] = out.final_grade - out.q1_term_grade
    return out


# ══════════════════════════════════════════════════════════════════════════════
# Models
# ══════════════════════════════════════════════════════════════════════════════
def candidates() -> dict:
    return {
        "Ridge Regression": lambda: make_pipeline(StandardScaler(), Ridge(alpha=1.0)),
        "Random Forest": lambda: RandomForestRegressor(
            n_estimators=400, min_samples_leaf=5, random_state=SEED, n_jobs=-1),
        "Extra Trees": lambda: ExtraTreesRegressor(
            n_estimators=400, min_samples_leaf=5, random_state=SEED, n_jobs=-1),
        "Gradient Boosting": lambda: GradientBoostingRegressor(
            n_estimators=200, max_depth=2, learning_rate=0.05, subsample=0.8,
            random_state=SEED),
    }


def oof_residuals(make, X, y, groups) -> np.ndarray:
    """The model's errors on learners it was not fitted on, within the
    training data. These set the range and the chance of failing."""
    out = np.zeros(len(y))
    for tr, te in GroupKFold(n_splits=4).split(X, y, groups):
        m = make().fit(X.iloc[tr], y[tr])
        out[te] = y[te] - m.predict(X.iloc[te])
    return out


def fail_chance(pred_final: np.ndarray, residuals: np.ndarray) -> np.ndarray:
    """P(final < 75): the share of past errors that would carry this
    prediction below the line. Empirical — no distribution is assumed."""
    r = np.sort(residuals)
    return np.searchsorted(r, PASSING - pred_final, side="left") / len(r)


def score(final, failed, pred_final, residuals) -> dict:
    err = final - pred_final
    lo_q, hi_q = np.quantile(residuals, [(1 - INTERVAL) / 2, 1 - (1 - INTERVAL) / 2])
    inside = (final >= pred_final + lo_q) & (final <= pred_final + hi_q)
    p_fail = fail_chance(pred_final, residuals)
    flag = p_fail >= 0.5
    tp = int((flag & (failed == 1)).sum())
    return {
        "mae": float(np.abs(err).mean()),
        "rmse": float(np.sqrt((err ** 2).mean())),
        "within_1": float((np.abs(err) <= 1).mean()),
        "within_2": float((np.abs(err) <= 2).mean()),
        "range_coverage": float(inside.mean()),
        "range_width": float(hi_q - lo_q),
        "fail_tp": tp,
        "fail_fn": int(((~flag) & (failed == 1)).sum()),
        "fail_fp": int((flag & (failed == 0)).sum()),
    }


def evaluate_split(df, tr, te) -> dict:
    X, y, g = df[FEATURES], df["change"].to_numpy(), df["learner_key"].to_numpy()
    q1 = df["q1_term_grade"].to_numpy()
    final, failed = df["final_grade"].to_numpy(), df["failed"].to_numpy()
    res = {}
    # Baseline: the final grade will equal the first term. Its range comes
    # from its own past errors, exactly as the models' do.
    base_resid = y[tr]  # the change itself is the baseline's error
    res["Baseline: final = Term 1 grade"] = score(final[te], failed[te], q1[te], base_resid)
    # Tougher baselines: grades tend to rise after the first term, and how much
    # may depend on the grade level. Neither needs a model; a model that does
    # not beat them has learned nothing beyond an average.
    shift = float(np.mean(y[tr]))
    res["Baseline: Term 1 + average rise"] = score(
        final[te], failed[te], q1[te] + shift, y[tr] - shift)
    level = df["grade_level"].to_numpy()
    by_level = pd.Series(y[tr]).groupby(level[tr]).mean().to_dict()
    level_shift_tr = np.array([by_level.get(v, shift) for v in level[tr]])
    level_shift_te = np.array([by_level.get(v, shift) for v in level[te]])
    res["Baseline: Term 1 + average rise for the grade level"] = score(
        final[te], failed[te], q1[te] + level_shift_te, y[tr] - level_shift_tr)
    for name, make in candidates().items():
        resid = oof_residuals(make, X.iloc[tr], y[tr], g[tr])
        m = make().fit(X.iloc[tr], y[tr])
        res[name] = score(final[te], failed[te], q1[te] + m.predict(X.iloc[te]), resid)
    return res


def summarise(runs: list[dict], sum_keys=("fail_tp", "fail_fn", "fail_fp")) -> pd.DataFrame:
    rows = []
    for name in runs[0]:
        vals = pd.DataFrame([r[name] for r in runs])
        row = {"model": name}
        for col in vals.columns:
            if col in sum_keys:
                row[col] = round(vals[col].sum() / REPEATS, 1)  # per full pass
            else:
                row[col] = round(vals[col].mean(), 3)
                if col == "mae":
                    row["mae_sd"] = round(vals[col].std(), 3)
        rows.append(row)
    return pd.DataFrame(rows).sort_values("mae")


# ══════════════════════════════════════════════════════════════════════════════
def main(src: Path) -> None:
    df = build(src)
    df.to_csv(ART / "final_dataset.csv", index=False)
    groups = df["learner_key"].to_numpy()

    say("FINAL-GRADE MODEL — first term in, final grade out")
    say("=" * 64)
    say(f"Learner-subject-years with a first term and a final grade: {len(df)}")
    say(f"Learners: {df.learner_key.nunique()}   Schools: {df.school.nunique()}")
    say(f"Failed (Remarks): {df.failed.sum()}   Final below 80: {(df.final_grade < 80).sum()}")
    say(f"Change from Term 1 to final: mean {df.change.mean():+.2f}, "
        f"sd {df.change.std():.2f}, range {df.change.min():+.0f} to {df.change.max():+.0f}")
    say(f"By school: {df.school.value_counts().to_dict()}")
    say(f"Features: {FEATURES}")

    runs = []
    for r in range(REPEATS):
        # GroupKFold is deterministic; shuffle the learner order per repeat.
        rng = np.random.default_rng(SEED + r)
        order = rng.permutation(len(df))
        shuffled = df.iloc[order].reset_index(drop=True)
        for tr, te in GroupKFold(n_splits=FOLDS).split(
                shuffled, groups=shuffled["learner_key"]):
            runs.append(evaluate_split(shuffled, tr, te))
    table = summarise(runs)
    say("")
    say(f"Grouped CV by learner, {REPEATS} x {FOLDS}-fold "
        f"(fail counts are per full pass over all {df.failed.sum()} failures)")
    say(table.to_string(index=False))

    # ── Unseen school: train on two, test on the third ─────────────────────────
    say("")
    say("Leave one school out — trained without that school, tested on it")
    transfer = {}
    for school in sorted(df.school.unique()):
        tr = np.where(df.school != school)[0]
        te = np.where(df.school == school)[0]
        res = evaluate_split(df, tr, te)
        transfer[school] = res
        say(f"\n  {school}: {len(te)} rows, {int(df.failed.iloc[te].sum())} failed")
        t = pd.DataFrame(res).T[["mae", "within_2", "range_coverage", "fail_tp", "fail_fn", "fail_fp"]]
        say("  " + t.round(3).to_string().replace("\n", "\n  "))

    # ── The deployed model ─────────────────────────────────────────────────────
    models_only = table[~table.model.str.startswith("Baseline")]
    best = models_only.iloc[0]["model"]
    baseline_mae = float(table[table.model.str.startswith("Baseline")]["mae"].iloc[0])
    make = candidates()[best]
    X, y = df[FEATURES], df["change"].to_numpy()
    residuals = oof_residuals(make, X, y, groups)
    model = make().fit(X, y)
    est = model[-1] if hasattr(model, "steps") else model
    if hasattr(est, "feature_importances_"):
        importances = np.asarray(est.feature_importances_)
    else:
        importances = np.abs(est.coef_) / np.abs(est.coef_).sum()

    say("")
    say(f"Deployed: {best} (MAE {float(models_only.iloc[0]['mae']):.3f} vs baseline "
        f"{baseline_mae:.3f}), fitted on all {len(df)} rows")
    say("Importances: " + ", ".join(
        f"{f}={v:.3f}" for f, v in sorted(zip(FEATURES, importances), key=lambda kv: -kv[1])))

    lo_q, hi_q = np.quantile(residuals, [(1 - INTERVAL) / 2, 1 - (1 - INTERVAL) / 2])
    friendly = {
        "q1_term_grade": "first-term grade",
        "q1_initial_grade": "first-term initial grade",
        "q1_ww_pct": "first-term Written Works",
        "q1_pt_pct": "first-term Performance Tasks",
        "q1_exam_pct": "first-term examination",
        "q1_component_gap": "uneven first-term components",
        "grade_level": "grade level",
    }
    interventions = {
        "first-term grade":
            "The whole first term sits low, and final grades rarely move far from it. "
            "Meet the learner (and the adviser) now and agree on a plan for the next term.",
        "first-term initial grade":
            "The first term's raw standing is low before transmutation. Review which "
            "component pulled it down and plan for the next term.",
        "first-term Written Works":
            "Written Works were the weak point in the first term. Check quiz and seatwork "
            "results with the learner and plan short practice before the next ones.",
        "first-term Performance Tasks":
            "Performance Tasks pulled the first term down. Look for missing or incomplete "
            "outputs and agree on a plan to submit the next ones on time.",
        "first-term examination":
            "The first-term examination was much weaker than the coursework. Review how "
            "the learner prepares for tests and revisit the topics they missed.",
        "uneven first-term components":
            "The first-term components are very uneven. Find out what held back the "
            "weakest one: absence, a missing output, or a topic not understood.",
        "grade level":
            "Review the learner's first-term record with the adviser.",
    }
    bundle = {
        "kind": "final_grade_regression",
        "model": model,
        "features": FEATURES,
        "importances": importances,
        # HIGH when the chance of failing is at least this.
        "threshold": 0.5,
        "friendly_names": friendly,
        "interventions": interventions,
        # Sorted out-of-fold errors: the range and the chance of failing are
        # read from these at prediction time.
        "residuals": np.sort(residuals),
        "interval": INTERVAL,
        "interval_offsets": [float(lo_q), float(hi_q)],
        "passing": PASSING,
        "medians": {f: float(df[f].median()) for f in FEATURES},
        "feature_means": {f: float(df[f].mean()) for f in FEATURES},
        "feature_stds": {f: float(df[f].std() or 1.0) for f in FEATURES},
    }
    dump(bundle, ART / "final_grade_model_v4.joblib")

    def records(t: pd.DataFrame) -> list:
        return json.loads(t.to_json(orient="records"))

    card = {
        "model": type(est).__name__,
        "version": "4.0.0-final-grade",
        "task": ("regression: predict a learner's final grade in a subject from "
                 "their first-term class record, as the change from the first-term "
                 "grade; report an 80% range and the chance the final grade falls "
                 f"below {PASSING}"),
        "target": "final grade = first-term grade + predicted change",
        "dataset": {
            "source": "Class_Record_Dataset_v6 (cleaned E-Class Records with final "
                      "grades and remarks), three schools",
            "rows_used": int(len(df)),
            "learners": int(df.learner_key.nunique()),
            "schools": df.school.value_counts().to_dict(),
            "failed": int(df.failed.sum()),
            "final_below_80": int((df.final_grade < 80).sum()),
            "subjects": df.subject.value_counts().to_dict(),
            "grade_levels": {str(int(k)): int(v)
                             for k, v in df.grade_level.value_counts().sort_index().items()},
        },
        "features_used": FEATURES,
        "features_excluded": {
            "Later terms": "they are what is being predicted",
            "Name": "hashed to a learner key for grouping, never stored",
            "Sex": "excluded as a fairness liability",
            "School / Section / Subject / School Year": "identifiers; including them "
                "would let the model memorise a cohort instead of reading the learner",
        },
        "protocol": {
            "splits": f"GroupKFold by learner, {FOLDS} folds x {REPEATS} shuffled repeats",
            "range_and_fail_chance": "from out-of-fold residuals on the training "
                                     "learners only (empirical, no distribution assumed)",
            "fail_flag": "chance of final < 75 at least 50%",
            "transfer": "leave one school out",
            "baseline": "final grade = first-term grade (a change of zero)",
        },
        "interval": INTERVAL,
        "interval_offsets": [round(float(lo_q), 3), round(float(hi_q), 3)],
        "results": records(table),
        "leave_one_school_out": {
            s: {m: {k: round(float(v), 4) for k, v in d.items()} for m, d in res.items()}
            for s, res in transfer.items()
        },
        "limitations": [
            f"Only {int(df.failed.sum())} learner-subject-years in the data failed, so "
            "the fail flag's catch rate rests on very few cases; the grade error "
            "(MAE) is measured on every learner and is the more reliable figure.",
            "The final grade is the mean of the term grades, so it contains the "
            "first term; the model is measured against simply carrying the "
            "first-term grade forward.",
            "Trained on records graded under older transmutation tables; the "
            "prediction is anchored on the school's own first-term grade to limit "
            "the effect.",
            "The export carries no attendance, so attendance plays no part.",
        ],
    }
    (ART / "model_card_v4.json").write_text(json.dumps(card, indent=2))

    # ── The fallback rule, for when the AI service cannot answer ───────────────
    # "Final grade = first-term grade + the average rise for that grade level."
    # It needs no model, so the backend can apply it on its own; it is fitted
    # on the same rows as the model so the two answer the same question on
    # the same footing. The range and the chance of failing come from the
    # rule's own errors, kept as 101 quantiles so the file stays small.
    overall_rise = float(df.change.mean())
    level_rise = df.groupby("grade_level").change.mean()
    rule_resid = df.change.to_numpy() - df.grade_level.map(level_rise).to_numpy()
    rule_lo, rule_hi = np.quantile(rule_resid, [(1 - INTERVAL) / 2, 1 - (1 - INTERVAL) / 2])
    rule = {
        "name": "first-term grade + average rise for the grade level",
        "version": "rule-v4",
        "passing": PASSING,
        "overall_rise": round(overall_rise, 4),
        "rise_by_grade_level": {str(int(k)): round(float(v), 4) for k, v in level_rise.items()},
        "interval": INTERVAL,
        "interval_offsets": [round(float(rule_lo), 4), round(float(rule_hi), 4)],
        "residual_quantiles": [round(float(v), 4)
                               for v in np.quantile(rule_resid, np.linspace(0, 1, 101))],
        "fitted_on_rows": int(len(df)),
    }
    (ART / "final_grade_rule.json").write_text(json.dumps(rule, indent=2))
    (ART / "results.txt").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "raw/Class_Record_Dataset_v6.xlsx"))
