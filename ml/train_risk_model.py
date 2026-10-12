"""
Train and evaluate the SmartPACE academic-risk classifier.

Run:  python train_risk_model.py

Reads  : classrecord_modelling.csv   (produced by prepare_dataset.py)
Writes : artifacts/risk_model.joblib       the deployed bundle
         artifacts/model_card.json         what it is and how it scored
         artifacts/algorithm_comparison.csv
         artifacts/threshold_sweep.csv
         artifacts/results.txt             the full readable record

The protocol below is the one the methodological audit arrived at, and the
parts that look fussy are the parts that were wrong the first time:

* Splits are grouped by learner. A learner contributes up to four quarter
  rows; letting the same learner sit in both training and test sets inflates
  every number reported.
* The decision threshold is chosen inside the training folds only. Choosing
  it on the test set is what produced the optimistic figures of the first
  model.
* Imputation medians are computed on training rows only.
* The arithmetic baseline is scored on the identical splits. The term grade
  is a weighted sum of these very components, so a model that cannot beat
  simple arithmetic on them has not earned its place in the system.
* Model-to-model comparisons use the Nadeau-Bengio corrected resampled
  t-test with a Holm-Bonferroni adjustment. An ordinary paired t-test
  assumes independent differences, which repeated splits of one dataset
  violate, and it reports significance that is not there.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from joblib import dump
from scipy import stats
from sklearn.ensemble import (ExtraTreesClassifier, GradientBoostingClassifier,
                              RandomForestClassifier)
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (average_precision_score, confusion_matrix, f1_score,
                             precision_score, recall_score, roc_auc_score)
from sklearn.model_selection import GroupShuffleSplit, StratifiedGroupKFold
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.tree import DecisionTreeClassifier

SEED = 42
N_SPLITS = 10
TEST_SIZE = 0.30
MIN_RECALL = 0.80          # a flag nobody receives is worthless
ART = Path("artifacts"); ART.mkdir(exist_ok=True)

FEATURES = ["ww_total", "ww_pct", "ww_weighted",
            "pt_total", "pt_pct", "pt_weighted",
            "pre_exam_weighted", "component_gap",
            "grade_level", "quarter"]
TARGET = "at_risk"
GROUP = "learner_key"

lines: list[str] = []
def say(s: str = "") -> None:
    print(s); lines.append(s)


def candidates() -> dict:
    """Five families, so the comparison can show whether the extra machinery
    of an ensemble is doing anything a single tree or a linear model is not."""
    return {
        "Random Forest": RandomForestClassifier(
            n_estimators=400, min_samples_leaf=2, class_weight="balanced",
            random_state=SEED, n_jobs=-1),
        "Extra Trees": ExtraTreesClassifier(
            n_estimators=400, min_samples_leaf=2, class_weight="balanced",
            random_state=SEED, n_jobs=-1),
        "Gradient Boosting": GradientBoostingClassifier(random_state=SEED),
        "Decision Tree": DecisionTreeClassifier(
            min_samples_leaf=5, class_weight="balanced", random_state=SEED),
        "Logistic Regression": Pipeline([
            ("scale", StandardScaler()),
            ("clf", LogisticRegression(max_iter=2000, class_weight="balanced",
                                       random_state=SEED))]),
    }


def pick_threshold(y: np.ndarray, p: np.ndarray) -> float:
    """Highest macro F1 among thresholds that still reach MIN_RECALL. If none
    does, fall back to the best recall available rather than silently
    accepting a threshold that flags almost nobody."""
    grid = np.round(np.arange(0.05, 0.96, 0.01), 2)
    best, best_score = 0.50, -1.0
    fallback, fallback_recall = 0.50, -1.0
    for t in grid:
        pred = (p >= t).astype(int)
        rec = recall_score(y, pred, zero_division=0)
        if rec > fallback_recall:
            fallback, fallback_recall = float(t), rec
        if rec >= MIN_RECALL:
            s = f1_score(y, pred, average="macro", zero_division=0)
            if s > best_score:
                best, best_score = float(t), s
    return best if best_score >= 0 else fallback


def threshold_from_training(model, X, y, groups) -> float:
    """Out-of-fold probabilities over the TRAINING students only, so the test
    set plays no part in choosing where the line falls."""
    inner = StratifiedGroupKFold(n_splits=5, shuffle=True, random_state=SEED)
    oof = np.zeros(len(y), dtype=float)
    for tr, va in inner.split(X, y, groups):
        m = clone_of(model)
        m.fit(X[tr], y[tr])
        oof[va] = m.predict_proba(X[va])[:, 1]
    return pick_threshold(y, oof)


def clone_of(model):
    from sklearn.base import clone
    return clone(model)


def best_arithmetic_cut(pre_train: np.ndarray, y_train: np.ndarray) -> float:
    """The strongest single cut on the pre-examination weighted total, fitted
    on training rows only so it is judged on the same terms as the models."""
    best, best_score = float(np.median(pre_train)), -1.0
    for t in np.unique(np.round(pre_train, 1)):
        pred = (pre_train < t).astype(int)
        s = f1_score(y_train, pred, average="macro", zero_division=0)
        if s > best_score:
            best, best_score = float(t), s
    return best


def scores(y, pred, prob) -> dict:
    tn, fp, fn, tp = confusion_matrix(y, pred, labels=[0, 1]).ravel()
    return {
        "macro_f1": f1_score(y, pred, average="macro", zero_division=0),
        "recall": recall_score(y, pred, zero_division=0),
        "precision": precision_score(y, pred, zero_division=0),
        "specificity": tn / (tn + fp) if tn + fp else 0.0,
        "roc_auc": roc_auc_score(y, prob) if prob is not None and len(set(y)) > 1 else np.nan,
        "pr_auc": average_precision_score(y, prob) if prob is not None else np.nan,
        "false_pos": int(fp), "false_neg": int(fn),
        "true_pos": int(tp), "true_neg": int(tn),
    }


def nadeau_bengio(diffs: np.ndarray, test_size: float, n: int):
    """Corrected resampled t-test. The variance of the mean difference is
    inflated by (1/n + test/train) because the splits share training data."""
    d = np.asarray(diffs, dtype=float)
    mean = d.mean()
    var = d.var(ddof=1)
    if var == 0:
        return mean, np.nan, np.nan
    factor = 1.0 / n + test_size / (1.0 - test_size)
    t = mean / np.sqrt(var * factor)
    p = 2 * (1 - stats.t.cdf(abs(t), df=n - 1))
    return mean, t, p


def holm(pvalues: dict) -> dict:
    """Holm-Bonferroni thresholds, smallest p-value tested most strictly."""
    ordered = sorted(pvalues.items(), key=lambda kv: kv[1])
    m = len(ordered)
    return {name: 0.05 / (m - i) for i, (name, _) in enumerate(ordered)}


def main() -> None:
    df = pd.read_csv("classrecord_modelling.csv")
    X_all = df[FEATURES].to_numpy(dtype=float)
    y_all = df[TARGET].to_numpy(dtype=int)
    g_all = df[GROUP].to_numpy()
    pre_all = df["pre_exam_weighted"].to_numpy(dtype=float)

    say("=" * 72)
    say("SmartPACE academic-risk classifier — training record")
    say("=" * 72)
    say(f"Rows {len(df)} | learners {df[GROUP].nunique()} | "
        f"at risk {y_all.sum()} ({100*y_all.mean():.2f}%)")
    say(f"Target: term grade < 80")
    say(f"Features ({len(FEATURES)}): {', '.join(FEATURES)}")
    say(f"Protocol: GroupShuffleSplit by learner, {N_SPLITS} splits, "
        f"test fraction {TEST_SIZE:.2f}; threshold chosen inside training folds "
        f"at minimum recall {MIN_RECALL}")
    say()

    splitter = GroupShuffleSplit(n_splits=N_SPLITS, test_size=TEST_SIZE,
                                 random_state=SEED)
    folds = list(splitter.split(X_all, y_all, groups=g_all))

    # leakage check, every split, every time
    for k, (tr, te) in enumerate(folds):
        assert set(g_all[tr]).isdisjoint(set(g_all[te])), f"split {k} shares a learner"
    say(f"Leakage check: no learner appears in both sides of any of the "
        f"{len(folds)} splits.")
    say()

    per_model: dict[str, list[dict]] = {n: [] for n in candidates()}
    per_model["Arithmetic rule"] = []
    thresholds: dict[str, list[float]] = {n: [] for n in candidates()}

    for tr, te in folds:
        Xtr, Xte = X_all[tr], X_all[te]
        ytr, yte = y_all[tr], y_all[te]
        gtr = g_all[tr]

        med = np.nanmedian(Xtr, axis=0)                 # training rows only
        Xtr = np.where(np.isnan(Xtr), med, Xtr)
        Xte = np.where(np.isnan(Xte), med, Xte)

        for name, proto in candidates().items():
            thr = threshold_from_training(proto, Xtr, ytr, gtr)
            model = clone_of(proto)
            model.fit(Xtr, ytr)
            prob = model.predict_proba(Xte)[:, 1]
            per_model[name].append(scores(yte, (prob >= thr).astype(int), prob))
            thresholds[name].append(thr)

        cut = best_arithmetic_cut(pre_all[tr], ytr)
        pred = (pre_all[te] < cut).astype(int)
        per_model["Arithmetic rule"].append(scores(yte, pred, None))

    say("-" * 72)
    say("Algorithm comparison — mean ± sd over the 10 splits")
    say("-" * 72)
    rows = []
    for name, rs in per_model.items():
        r = pd.DataFrame(rs)
        rows.append({
            "model": name,
            "macro_f1": r["macro_f1"].mean(), "macro_f1_sd": r["macro_f1"].std(ddof=1),
            "recall": r["recall"].mean(), "recall_sd": r["recall"].std(ddof=1),
            "precision": r["precision"].mean(), "precision_sd": r["precision"].std(ddof=1),
            "specificity": r["specificity"].mean(),
            "roc_auc": r["roc_auc"].mean(),
            "pr_auc": r["pr_auc"].mean(),
            "false_pos": r["false_pos"].mean(), "false_neg": r["false_neg"].mean(),
        })
    comp = pd.DataFrame(rows).sort_values("macro_f1", ascending=False).reset_index(drop=True)
    say(comp.round(4).to_string(index=False))
    say()

    leader = comp.iloc[0]["model"]
    say(f"Highest mean macro F1: {leader}")
    say()
    say("-" * 72)
    say("Paired comparison against the leader (Nadeau-Bengio, Holm-adjusted)")
    say("-" * 72)
    lead = np.array([r["macro_f1"] for r in per_model[leader]])
    pvals, stats_row = {}, {}
    for name, rs in per_model.items():
        if name == leader:
            continue
        other = np.array([r["macro_f1"] for r in rs])
        d = lead - other
        mean, t, p = nadeau_bengio(d, TEST_SIZE, N_SPLITS)
        sh = stats.shapiro(d).pvalue if np.ptp(d) > 0 else np.nan
        pvals[name] = 1.0 if np.isnan(p) else p
        stats_row[name] = (mean, p, sh)
    thr_holm = holm(pvals)
    say(f"{'comparison':<22}{'Δ macro F1':>12}{'p (corrected)':>16}"
        f"{'Holm threshold':>16}{'reject H0':>12}{'Shapiro p':>12}")
    sig_any = False
    for name in sorted(stats_row, key=lambda k: pvals[k]):
        mean, p, sh = stats_row[name]
        rej = (not np.isnan(p)) and p < thr_holm[name]
        sig_any = sig_any or rej
        say(f"{leader+' vs '+name:<22}{mean:>12.4f}{p:>16.4f}"
            f"{thr_holm[name]:>16.4f}{('yes' if rej else 'no'):>12}{sh:>12.3f}")
    say()
    say("No difference reaches significance." if not sig_any else
        "At least one difference reaches significance — see the table above.")
    say("Failing to reject is not evidence of equivalence; ten splits were "
        "never enough to demonstrate that.")
    say()

    # ── temporal split: does it transfer to a cohort it has not seen? ─────
    say("-" * 72)
    say("Temporal validation — train on 2022-2024, test on 2025-2026")
    say("-" * 72)
    is_old = df["school_year"].isin(["2022-2023", "2023-2024"]).to_numpy()
    Xo, yo, go = X_all[is_old], y_all[is_old], g_all[is_old]
    Xn, yn = X_all[~is_old], y_all[~is_old]
    med = np.nanmedian(Xo, axis=0)
    Xo = np.where(np.isnan(Xo), med, Xo); Xn = np.where(np.isnan(Xn), med, Xn)
    proto = candidates()["Random Forest"]
    thr_t = threshold_from_training(proto, Xo, yo, go)
    m = clone_of(proto); m.fit(Xo, yo)
    prob_n = m.predict_proba(Xn)[:, 1]
    s_old_thr = scores(yn, (prob_n >= thr_t).astype(int), prob_n)
    say(f"Training rows {is_old.sum()} (risk rate {yo.mean():.3f}); "
        f"test rows {(~is_old).sum()} (risk rate {yn.mean():.3f})")
    say(f"Threshold carried over from the earlier cohort: {thr_t:.2f}")
    say(f"  macro F1 {s_old_thr['macro_f1']:.3f} | recall {s_old_thr['recall']:.3f} | "
        f"precision {s_old_thr['precision']:.3f} | ROC AUC {s_old_thr['roc_auc']:.3f}")
    thr_re = pick_threshold(yn, prob_n)
    s_new_thr = scores(yn, (prob_n >= thr_re).astype(int), prob_n)
    say(f"Threshold refitted on the new cohort: {thr_re:.2f} "
        f"(an upper bound, not an achievable result)")
    say(f"  macro F1 {s_new_thr['macro_f1']:.3f} | recall {s_new_thr['recall']:.3f} | "
        f"precision {s_new_thr['precision']:.3f}")
    say("The gap between those two lines is what annual recalibration buys.")
    say()

    # ── the deployed model ────────────────────────────────────────────────
    say("-" * 72)
    say("Deployed model")
    say("-" * 72)
    med_all = np.nanmedian(X_all, axis=0)
    Xf = np.where(np.isnan(X_all), med_all, X_all)
    # Fitted on a named frame, not a bare array, so scikit-learn checks the
    # column names at inference instead of trusting the service to send the
    # ten features in the right order.
    Xf_named = pd.DataFrame(Xf, columns=FEATURES)
    final = candidates()["Random Forest"]
    thr_final = float(np.median(thresholds["Random Forest"]))
    final.fit(Xf_named, y_all)
    say(f"Random Forest retained. Across the 10 splits the thresholds chosen "
        f"inside training folds were {sorted(thresholds['Random Forest'])}; "
        f"the median, {thr_final:.2f}, is deployed.")

    imp = pd.Series(final.feature_importances_, index=FEATURES).sort_values(ascending=False)
    say("\nImpurity importance (ranking only — it is biased toward "
        "high-cardinality numeric features):")
    say(imp.round(4).to_string())

    rf_row = comp[comp["model"] == "Random Forest"].iloc[0]
    rule_row = comp[comp["model"] == "Arithmetic rule"].iloc[0]
    say(f"\nAgainst the arithmetic baseline, per split on average:")
    say(f"  macro F1   model {rf_row['macro_f1']:.3f}  rule {rule_row['macro_f1']:.3f}")
    say(f"  recall     model {rf_row['recall']:.3f}  rule {rule_row['recall']:.3f}")
    say(f"  precision  model {rf_row['precision']:.3f}  rule {rule_row['precision']:.3f}")
    say(f"  false alarms per split: model {rf_row['false_pos']:.1f}  "
        f"rule {rule_row['false_pos']:.1f}")
    say(f"  missed learners per split: model {rf_row['false_neg']:.1f}  "
        f"rule {rule_row['false_neg']:.1f}")

    dump({"model": final, "features": FEATURES, "threshold": thr_final,
          "medians": med_all.tolist(), "risk_below": 80,
          "importances": imp.to_dict(), "version": "2.0.0-classrecord"},
         ART / "risk_model.joblib")
    comp.to_csv(ART / "algorithm_comparison.csv", index=False)

    # Threshold sweep, reusing one set of per-fold probabilities rather than
    # refitting the forest once per threshold.
    fold_probs = []
    for tr, te in folds:
        mm = clone_of(candidates()["Random Forest"])
        mm.fit(Xf[tr], y_all[tr])
        fold_probs.append((y_all[te], mm.predict_proba(Xf[te])[:, 1]))
    sweep = []
    for t in np.round(np.arange(0.05, 0.96, 0.05), 2):
        r = pd.DataFrame([scores(yy, (pp >= t).astype(int), pp)
                          for yy, pp in fold_probs])
        sweep.append({"threshold": t, "macro_f1": r["macro_f1"].mean(),
                      "recall": r["recall"].mean(), "precision": r["precision"].mean(),
                      "false_pos": r["false_pos"].mean(), "false_neg": r["false_neg"].mean()})
    pd.DataFrame(sweep).to_csv(ART / "threshold_sweep.csv", index=False)

    card = {
        "model": "RandomForestClassifier",
        "version": "2.0.0-classrecord",
        "task": f"binary classification of end-of-term academic risk "
                f"(term grade < 80) from class-record components recorded "
                f"before the term examination",
        "dataset": {
            "source": "Merged E-Class Record export, Looc National High School, "
                      "15 class records, SY 2022-2023 / 2023-2024 / 2025-2026",
            "rows_in_source": 1237,
            "rows_used": int(len(df)),
            "excluded_term_not_graded": 78,
            "excluded_blank_component": 65,
            "learners": int(df[GROUP].nunique()),
            "at_risk": int(y_all.sum()),
            "at_risk_rate": round(float(y_all.mean()), 4),
            "below_passing_mark_75": int((df["term_grade"] < 75).sum()),
            "balancing_applied": "none",
        },
        "features_used": FEATURES,
        "features_excluded": {
            "Term Exam Score / Percentage / Weighted":
                "not known until the term examination is sat; it is part of "
                "the outcome the model is asked to anticipate",
            "Initial Grade": "the weighted sum that the target is derived from",
            "Term Grade": "the target itself",
            "Name": "personal information; hashed to a learner key for grouping "
                    "and never stored",
            "Sex": "excluded as a fairness liability, not for want of signal",
            "Section / Subject / School Year":
                "identifiers; the file is 95.6% Filipino and 86.8% Grade 8, so "
                "these would encode the cohort rather than the learner",
        },
        "protocol": {
            "splits": f"GroupShuffleSplit by learner, {N_SPLITS} splits, "
                      f"test fraction {TEST_SIZE}",
            "threshold_selection": f"inner StratifiedGroupKFold on training "
                                   f"learners, macro F1 subject to recall "
                                   f"≥ {MIN_RECALL}",
            "imputation": "median of training rows only",
            "significance": "Nadeau-Bengio corrected resampled t-test with "
                            "Holm-Bonferroni adjustment",
        },
        "threshold": thr_final,
        "results": comp.round(4).to_dict(orient="records"),
        "temporal_validation": {
            "train": "2022-2023 and 2023-2024", "test": "2025-2026",
            "threshold_carried_over": round(thr_t, 2),
            "with_carried_threshold": {k: round(float(v), 4) for k, v in s_old_thr.items()},
            "with_refitted_threshold": {k: round(float(v), 4) for k, v in s_new_thr.items()},
        },
        "limitations": [
            "The term grade is a weighted sum of the components used as "
            "features, so the task is substantially arithmetic. The arithmetic "
            "baseline in the comparison table is the honest point of reference, "
            "not a naive control.",
            "No attendance data exists in this export, so attendance plays no "
            "part in the model.",
            "86.8% of rows are Grade 8 and 95.6% are Filipino. Conclusions do "
            "not extend to other grade levels or subjects.",
            "Performance is established at the point where Written Works and "
            "Performance Tasks are complete and the term examination is not. "
            "No claim is made about earlier points in the term.",
        ],
    }
    (ART / "model_card.json").write_text(json.dumps(card, indent=2))
    (ART / "results.txt").write_text("\n".join(lines) + "\n")
    say(f"\nwrote artifacts/risk_model.joblib, model_card.json, "
        f"algorithm_comparison.csv, threshold_sweep.csv, results.txt")


if __name__ == "__main__":
    main()
