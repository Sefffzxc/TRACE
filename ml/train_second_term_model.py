"""Train compatible first- and second-period final-grade checkpoints.

Run from ml/: .venv-ml/Scripts/python.exe -I train_second_term_model.py
The private workbook stays in raw/. Only pseudonymous paired records and
aggregate validation results are written. Legacy v4 artifacts remain as history.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from joblib import dump
from sklearn.ensemble import ExtraTreesRegressor, RandomForestRegressor
from sklearn.linear_model import Ridge
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parent
FEATURES = [f"q{n}_{suffix}" for n in (1, 2) for suffix in (
    "term_grade", "initial_grade", "ww_pct", "pt_pct", "exam_pct", "component_gap"
)] + ["grade_level"]
PASSING = 75
INTERVAL = 0.8
SEED = 42


def build(source: Path) -> pd.DataFrame:
    raw = pd.read_excel(source, sheet_name="Cleaned Data")
    raw["learner_key"] = [hashlib.sha256(
        f"smartpace::{str(s).strip().upper()}::{str(n).strip().upper()}".encode()
    ).hexdigest()[:16] for n, s in zip(raw.Name, raw.School)]
    raw = raw.drop(columns=["Name", "Sex", "ID"])
    key = ["learner_key", "School", "Subject", "School Year", "Grade Level"]
    columns = {"Term Grade": "term_grade", "Initial Grade": "initial_grade",
               "Written Works Percentage Score": "ww_pct",
               "Performance Task Percentage Score": "pt_pct",
               "Term Exam Percentage Score": "exam_pct"}
    frames = []
    for period in (1, 2):
        frame = raw.loc[raw.Quarter == period, key + list(columns) + ["Final Grade"]]
        # Identical duplicates are harmless; conflicting duplicates are ambiguous.
        frame = frame.drop_duplicates()
        if frame.duplicated(key).any():
            raise ValueError(f"Conflicting duplicate period {period} records")
        frame = frame.rename(columns={c: f"q{period}_{v}" for c, v in columns.items()})
        frame = frame.rename(columns={"Final Grade": f"final_{period}"})
        parts = frame[[f"q{period}_{s}" for s in ("ww_pct", "pt_pct", "exam_pct")]]
        frame[f"q{period}_component_gap"] = (parts.max(axis=1) - parts.min(axis=1)).round(2)
        frames.append(frame)
    paired = frames[0].merge(frames[1], on=key, validate="one_to_one")
    if not np.allclose(paired.final_1, paired.final_2):
        raise ValueError("Paired records disagree on the final-grade label")
    paired = paired.rename(columns={"School": "school", "Subject": "subject",
        "School Year": "school_year", "Grade Level": "grade_level", "final_1": "final_grade"})
    paired = paired.drop(columns="final_2")
    # A live MATATAG year has three terms. The workbook's Final Grade may
    # include a fourth quarter, so it is not the target for this checkpoint.
    third = raw.loc[raw.Quarter == 3, key + ["Term Grade"]].drop_duplicates()
    if third.duplicated(key).any():
        raise ValueError("Conflicting duplicate third-period grades")
    third = third.rename(columns={"School": "school", "Subject": "subject",
        "School Year": "school_year", "Grade Level": "grade_level", "Term Grade": "third_grade"})
    paired = paired.merge(third, on=["learner_key", "school", "subject", "school_year", "grade_level"],
                          validate="one_to_one")
    paired["final_grade"] = (paired.q1_term_grade + paired.q2_term_grade + paired.third_grade) / 3
    paired = paired.drop(columns="third_grade")
    paired[FEATURES + ["final_grade"]] = paired[FEATURES + ["final_grade"]].astype(float)
    valid = np.isfinite(paired[FEATURES + ["final_grade"]]).all(axis=1)
    paired = paired.loc[valid].reset_index(drop=True)
    paired["anchor"] = (paired.q1_term_grade + paired.q2_term_grade) / 2
    paired["change"] = paired.final_grade - paired.anchor
    return paired


def candidates():
    return {
        "Ridge Regression": lambda: make_pipeline(StandardScaler(), Ridge(alpha=10)),
        "Extra Trees": lambda: ExtraTreesRegressor(n_estimators=200,
            min_samples_leaf=5, random_state=SEED, n_jobs=-1),
        "Random Forest": lambda: RandomForestRegressor(n_estimators=200,
            min_samples_leaf=5, random_state=SEED, n_jobs=-1),
    }


def splits(df, folds=5):
    return GroupKFold(n_splits=folds).split(df, groups=df.learner_key)


def rule_change(train, test):
    means = train.groupby("grade_level").change.mean()
    return test.grade_level.map(means).fillna(train.change.mean()).to_numpy()


def oof(df, make=None, features=FEATURES):
    predictions = np.zeros(len(df))
    for tr, te in splits(df, 4):
        train, test = df.iloc[tr], df.iloc[te]
        predictions[te] = (make().fit(train[features], train.change).predict(test[features])
                           if make else rule_change(train, test))
    return df.change.to_numpy() - predictions


def metrics(actual, predicted, residuals):
    low, high = np.quantile(residuals, [0.1, 0.9])
    chance = np.searchsorted(np.sort(residuals), PASSING - predicted, side="left") / len(residuals)
    range_low = np.round(np.clip(predicted + low, 60, 100), 1)
    range_high = np.round(np.clip(predicted + high, 60, 100), 1)
    predicted = np.round(np.clip(predicted, 60, 100), 1)
    err = actual - predicted
    high_risk, failed = chance >= 0.5, actual < PASSING
    return {"mae": float(np.abs(err).mean()), "rmse": float(np.sqrt(np.mean(err ** 2))),
        "within_1": float((np.abs(err) <= 1).mean()),
        "within_2": float((np.abs(err) <= 2).mean()),
        "range_coverage": float(((actual >= range_low) & (actual <= range_high)).mean()),
        "fail_tp": int((high_risk & failed).sum()), "fail_fn": int((~high_risk & failed).sum()),
        "fail_fp": int((high_risk & ~failed).sum())}


def evaluate(train, test, features=FEATURES):
    out = {}
    for name, make in candidates().items():
        residuals = oof(train, make, features)
        prediction = test.anchor.to_numpy() + make().fit(train[features], train.change).predict(test[features])
        out[name] = metrics(test.final_grade.to_numpy(), prediction, residuals)
    baseline_name = "mean of first two periods" if "q2_term_grade" in features else "first-period grade"
    out[f"Baseline: {baseline_name}"] = metrics(test.final_grade.to_numpy(),
        test.anchor.to_numpy(), train.change.to_numpy())
    if "q2_term_grade" in features:
        carry_train = (train.q1_term_grade + 2 * train.q2_term_grade) / 3
        carry_test = (test.q1_term_grade + 2 * test.q2_term_grade) / 3
        out["Baseline: carry second-period grade into third"] = metrics(test.final_grade.to_numpy(),
            carry_test.to_numpy(), (train.final_grade - carry_train).to_numpy())
    out[f"Baseline: {baseline_name} + average rise for the grade level"] = metrics(
        test.final_grade.to_numpy(), test.anchor.to_numpy() + rule_change(train, test), oof(train))
    return out


def train_checkpoint(source, checkpoint):
    df = source.copy()
    checkpoint_name = "second_term" if checkpoint == 2 else "first_term"
    version = f"5.0.0-{checkpoint_name.replace('_', '-')}"
    art = ROOT / f"artifacts_{checkpoint_name}"
    art.mkdir(exist_ok=True)
    features = FEATURES if checkpoint == 2 else [f for f in FEATURES if not f.startswith("q2_")]
    if checkpoint == 1:
        df["anchor"] = df.q1_term_grade
        df["change"] = df.final_grade - df.anchor
    df.to_csv(art / "paired_dataset.csv", index=False)
    runs = []
    for fold, (tr, te) in enumerate(splits(df), 1):
        print(f"{checkpoint_name}: validating fold {fold}/5", flush=True)
        runs.append((len(te), evaluate(df.iloc[tr], df.iloc[te], features)))
    results = []
    for name in runs[0][1]:
        row = {"model": name}
        for key in runs[0][1][name]:
            values = [r[name][key] for _, r in runs]
            row[key] = (sum(values) if key.startswith("fail_") else
                        sum(n * r[name][key] for n, r in runs) / len(df))
        results.append(row)
    results.sort(key=lambda r: r["mae"])
    best = next(r["model"] for r in results if r["model"] in candidates())
    print(f"Selected {best}; evaluating school transfer", flush=True)
    transfer = {}
    for school in sorted(df.school.unique()):
        transfer[school] = evaluate(df.loc[df.school != school], df.loc[df.school == school], features)
    make = candidates()[best]
    residuals = oof(df, make, features)
    model = make().fit(df[features], df.change)
    estimator = model[-1] if hasattr(model, "steps") else model
    importance = (estimator.feature_importances_ if hasattr(estimator, "feature_importances_")
                  else np.abs(estimator.coef_))
    importance = importance / importance.sum()
    friendly = {"grade_level": "grade level"}
    for n, label in ((1, "first"), (2, "second")):
        friendly.update({f"q{n}_term_grade": f"{label}-period grade",
            f"q{n}_initial_grade": f"{label}-period initial grade",
            f"q{n}_ww_pct": f"{label}-period Written Works",
            f"q{n}_pt_pct": f"{label}-period Performance Tasks",
            f"q{n}_exam_pct": f"{label}-period examinations",
            f"q{n}_component_gap": f"uneven {label}-period components"})
    actions = {label: "Review both completed periods with the learner and adviser. "
        "Focus practice and support on the weakest component before the next assessments."
        for label in friendly.values()}
    offsets = [float(v) for v in np.quantile(residuals, [0.1, 0.9])]
    bundle = {"kind": "final_grade_regression", "version": version,
        "anchor_features": [f"q{n}_term_grade" for n in range(1, checkpoint + 1)], "model": model,
        "features": features, "importances": importance, "threshold": 0.5,
        "friendly_names": friendly, "interventions": actions, "residuals": np.sort(residuals),
        "interval": INTERVAL, "interval_offsets": offsets, "passing": PASSING,
        "medians": {f: float(df[f].median()) for f in features},
        "feature_means": {f: float(df[f].mean()) for f in features},
        "feature_stds": {f: float(df[f].std() or 1) for f in features}}
    baseline_name = "mean of first two periods" if checkpoint == 2 else "first-period grade"
    card = {"model": type(estimator).__name__, "version": version,
        "task": f"Predict final subject grade from {checkpoint} completed period(s); report an 80% range and chance of failing.",
        "target": "Three-period final grade: mean of grades in periods 1, 2 and 3",
        "dataset": {"source": "Class_Record_Dataset_v6; observed periods 1, 2 and 3 in the same learner-subject-year",
            "rows_used": len(df), "learners": int(df.learner_key.nunique()),
            "grade_levels": {str(int(k)): int(v) for k, v in df.grade_level.value_counts().sort_index().items()},
            "failed": int((df.final_grade < PASSING).sum()),
            "schools": df.school.value_counts().to_dict()},
        "features_used": features,
        "features_excluded": {"Later periods and final grade": "labels only; unavailable at prediction time",
            "Names, sex and identifiers": "not model inputs"},
        "protocol": {"splits": "5-fold GroupKFold by learner; all subjects and years of each learner stay together",
            "range_and_fail_chance": "inner 4-fold learner-group out-of-fold residuals within each training fold",
            "transfer": "leave one school out; learner-group calibration on remaining schools",
            "baseline": f"Baseline: {baseline_name} + average rise for the grade level"},
        "interval": INTERVAL, "interval_offsets": offsets, "results": results,
        "leave_one_school_out": transfer,
        "limitations": [
            "The target averages historical periods 1, 2 and 3 only; fourth-quarter results and workbook final grades are excluded.",
            "Historical periods include quarters and older transmutation tables; performance in the live MATATAG setting has not been validated.",
            "Only a small number of final grades failed; failure probabilities are less certain than grade errors.",
            "Complete three-period records were available only from two elementary schools. Neither checkpoint is validated for Grade 8 or other high-school grades.",
            f"This checkpoint requires {checkpoint} completed period(s) in the same subject and school-year enrollment.",
            "A second checkpoint has separate validation results; its interval is not a guarantee."]}
    rule_residuals = oof(df)
    rule = {"name": f"{baseline_name} + average rise for the grade level",
        "version": f"rule-v5-{checkpoint_name.replace('_', '-')}", "passing": PASSING,
        "overall_rise": float(df.change.mean()),
        "rise_by_grade_level": {str(int(k)): float(v) for k, v in df.groupby("grade_level").change.mean().items()},
        "interval": INTERVAL, "interval_offsets": [float(v) for v in np.quantile(rule_residuals, [0.1, 0.9])],
        "residual_quantiles": [float(v) for v in np.quantile(rule_residuals, np.linspace(0, 1, 101))],
        "fitted_on_rows": len(df), "calibration": "4-fold learner-group out-of-fold rule errors"}
    dump(bundle, art / f"final_grade_model_v5_{checkpoint_name}.joblib", compress=3)
    (art / f"model_card_v5_{checkpoint_name}.json").write_text(json.dumps(card, indent=2), encoding="utf-8")
    (art / f"final_grade_rule_{checkpoint_name}.json").write_text(json.dumps(rule, indent=2), encoding="utf-8")
    model_dir = ROOT.parent / "ai-service/model"
    dump(bundle, model_dir / f"final_grade_model_v5_{checkpoint_name}.joblib", compress=3)
    (model_dir / f"model_card_v5_{checkpoint_name}.json").write_text(json.dumps(card, indent=2), encoding="utf-8")
    (ROOT.parent / f"backend/app/services/final_grade_rule_{checkpoint_name}.json").write_text(
        json.dumps(rule, indent=2), encoding="utf-8")
    report = pd.DataFrame(results).round(4).to_string(index=False)
    (art / "results.txt").write_text(f"{len(df)} paired rows; {df.learner_key.nunique()} learners\n"
        f"Selected: {best}\n{report}\n", encoding="utf-8")
    print(report, flush=True)


if __name__ == "__main__":
    dataset = build(ROOT / "raw/Class_Record_Dataset_v6.xlsx")
    for period in (1, 2):
        train_checkpoint(dataset, period)
