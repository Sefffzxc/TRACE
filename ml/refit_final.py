"""Refit only the deployed estimator on a named frame, leaving every reported
number untouched. The evaluation in train_risk_model.py produced the figures
in the results file; this changes nothing about them, it only makes the
deployed model validate its input column names instead of trusting order."""
import numpy as np, pandas as pd
from joblib import load, dump
from sklearn.ensemble import RandomForestClassifier
from pathlib import Path

ART = Path("artifacts")
b = load(ART / "risk_model.joblib")
F = b["features"]
d = pd.read_csv("classrecord_modelling.csv")
X = d[F].astype(float)
X = X.fillna(X.median())
y = d["at_risk"].to_numpy(int)

m = RandomForestClassifier(n_estimators=400, min_samples_leaf=2,
                           class_weight="balanced", random_state=42, n_jobs=-1)
m.fit(X, y)

before = b["model"].predict_proba(X.to_numpy())[:, 1]
after = m.predict_proba(X)[:, 1]
print("max probability change from the refit: %.2e" % np.abs(before - after).max())
print("decisions that changed:", int(((before >= b["threshold"]) != (after >= b["threshold"])).sum()))

b["model"] = m
b["importances"] = [float(v) for v in m.feature_importances_]
dump(b, ART / "risk_model.joblib")
print("refitted and saved")
