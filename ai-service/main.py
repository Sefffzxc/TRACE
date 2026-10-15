"""
LBCA Academic Risk Classification Service
==========================================
A standalone inference service. It holds the trained model and nothing else:
no database, no authentication of users, no business rules. The monitoring
backend calls it over HTTP and stores whatever comes back.

Keeping it separate means the model can be retrained and redeployed without
touching the monitoring system, and the manuscript can describe a genuine
three-tier architecture.

What it will and will not say
-----------------------------
From a learner's FIRST-TERM class record in a subject, the model predicts
their FINAL grade in it: an expected grade, an 80% range, and the chance that
the final grade falls below the passing mark of 75. It is a regression model
(Extra Trees) that predicts the change from the first-term grade, so the
answer stays anchored on the school's own first-term grade.

The range and the chance of failing come from the model's own errors on
learners it was not trained on, not from an assumed distribution. On the
training data it is only marginally more accurate than the rule "first-term
grade plus the average rise for the grade level", and it is only valid for
the schools and grade levels it was trained on; `/model-card` reports both.
Every response carries `model_version` so a stored prediction can always be
traced back to the model that made it.

Endpoints
---------
    GET  /health         liveness + whether the model loaded
    GET  /model-card     metrics, features, threshold, limitations
    POST /predict        one student
    POST /predict/batch  up to 500 students in one call

Auth
----
If AI_SERVICE_TOKEN is set, every /predict call must send
`X-Service-Token: <token>`. Render injects it into both services from the
blueprint, so the two agree without anything being committed.
"""
from __future__ import annotations

import json
import os
import pathlib
import time
from contextlib import asynccontextmanager
from typing import List, Optional

import joblib
import numpy as np
import pandas as pd
from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field

MODEL_DIR = pathlib.Path(os.getenv("MODEL_DIR", "model"))
MODEL_PATH = MODEL_DIR / "final_grade_model_v4.joblib"
CARD_PATH = MODEL_DIR / "model_card_v4.json"
MODEL_VERSION = os.getenv("MODEL_VERSION", "v4.0.0-final-grade")
SERVICE_TOKEN = os.getenv("AI_SERVICE_TOKEN", "").strip()
MAX_BATCH = 500

STATE: dict = {"bundle": None, "card": None, "loaded_at": None, "error": None}


# ══════════════════════════════════════════════════════════════════════════════
# Model loading
# ══════════════════════════════════════════════════════════════════════════════
def load_model() -> None:
    try:
        bundle_ = joblib.load(MODEL_PATH)
        # The training script stores the importances with the model, because
        # the deployed estimator may be a linear model with no
        # `feature_importances_`. On a forest that attribute is also a
        # property recomputed over every tree on each read, which made it
        # costlier than the predictions — so it is read once, here, either way.
        if bundle_.get("importances") is None:
            bundle_["importances"] = bundle_["model"].feature_importances_
        bundle_["importances"] = np.asarray(bundle_["importances"])
        STATE["bundle"] = bundle_
        STATE["loaded_at"] = time.time()
        STATE["error"] = None
        print(f"[ai] loaded {MODEL_PATH} — {len(STATE['bundle']['features'])} features, "
              f"threshold {STATE['bundle']['threshold']}", flush=True)
    except Exception as exc:  # noqa: BLE001 - surfaced through /health
        STATE["error"] = f"{type(exc).__name__}: {exc}"
        print(f"[ai] FAILED to load model: {STATE['error']}", flush=True)

    if CARD_PATH.is_file():
        try:
            STATE["card"] = json.loads(CARD_PATH.read_text())
        except Exception:  # noqa: BLE001
            STATE["card"] = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    load_model()
    yield


app = FastAPI(
    title="LBCA Academic Risk Classification Service",
    version=MODEL_VERSION,
    description="Inference-only service for the LBCA monitoring system.",
    lifespan=lifespan,
)

_origins = os.getenv("ALLOWED_ORIGINS", "*")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if _origins.strip() == "*" else
                  [o.strip() for o in _origins.split(",") if o.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
)


async def require_token(x_service_token: Optional[str] = Header(default=None)) -> None:
    """No-op when AI_SERVICE_TOKEN is unset, so local development stays simple."""
    if not SERVICE_TOKEN:
        return
    if x_service_token != SERVICE_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-Service-Token.",
        )


def bundle() -> dict:
    if STATE["bundle"] is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Model not loaded. {STATE['error'] or 'Unknown error.'}",
        )
    return STATE["bundle"]


# ══════════════════════════════════════════════════════════════════════════════
# Schemas — field names mirror the dataset columns exactly
# ══════════════════════════════════════════════════════════════════════════════
class StudentRecord(BaseModel):
    """One learner, one subject, read off their FIRST-TERM class record.

    The first-term grade and initial grade, and all three components as
    percentage scores. The prediction is the CHANGE from the first-term grade,
    added back onto it, so the answer stays on the school's own scale.

    Any field may be omitted; the service substitutes the training median and
    says so in the response. An UNKNOWN field is refused rather than ignored:
    a caller still sending an earlier model's inputs would otherwise get every
    input replaced by a median and a confident-looking score back. Refused,
    it falls back to its own rule and says so.
    """

    model_config = ConfigDict(extra="forbid")

    # Not capped at 100: a teacher can award a bonus above the highest
    # possible score, and capping here would reject a figure the class record
    # itself produced.
    q1_term_grade: Optional[float] = Field(default=None, ge=0, le=100,
        description="First-term grade (transmuted), as the school reports it")
    q1_initial_grade: Optional[float] = Field(default=None, ge=0, le=200,
        description="First-term initial grade, before transmutation")
    q1_ww_pct: Optional[float] = Field(default=None, ge=0, le=200,
        description="First-term Written Works percentage score")
    q1_pt_pct: Optional[float] = Field(default=None, ge=0, le=200,
        description="First-term Performance Tasks percentage score")
    q1_exam_pct: Optional[float] = Field(default=None, ge=0, le=200,
        description="First-term examinations percentage score")
    q1_component_gap: Optional[float] = Field(default=None, ge=0, le=200,
        description="Highest minus lowest of the three percentages")
    grade_level: Optional[float] = Field(default=None, ge=1, le=12)

    # Echoed back untouched so the caller can match responses to its own rows.
    reference: Optional[str] = Field(default=None, max_length=120)


class Prediction(BaseModel):
    risk_score: float = Field(
        description="Chance that the final grade falls below 75, 0-1")
    risk_level: str = Field(
        description="HIGH when that chance is at least the service threshold, else LOW")
    predicted_final: float = Field(description="Expected final grade")
    range_low: float = Field(description="Low end of the likely (80%) range")
    range_high: float = Field(description="High end of the likely (80%) range")
    primary_factor: Optional[str] = Field(
        default=None, description="Biggest contributing feature; null when LOW")
    suggested_action: Optional[str] = None
    used_threshold: float
    used_baseline_defaults: bool = Field(
        description="True if any feature was missing and a training median was substituted")
    imputed_fields: List[str] = []
    model_version: str
    reference: Optional[str] = None


class BatchRequest(BaseModel):
    records: List[StudentRecord]


class BatchResponse(BaseModel):
    predictions: List[Prediction]
    count: int


# ══════════════════════════════════════════════════════════════════════════════
# Inference
# ══════════════════════════════════════════════════════════════════════════════
def _prepare(rec: StudentRecord, b: dict) -> tuple:
    """Turn one request into the model's row, noting what had to be imputed.

    Request fields carry the training column names exactly, so the bundle's
    own feature list is the only mapping needed.
    """
    medians = b["medians"]
    row, imputed = {}, []
    for column in b["features"]:
        value = getattr(rec, column, None)
        if value is None:
            row[column] = medians[column]
            imputed.append(column)
        else:
            row[column] = float(value)
    return row, imputed


def _explain(row: dict, b: dict) -> tuple:
    """Why this student — the feature furthest from the cohort mean, weighted
    by its importance. This answers "why this one", not "what matters in
    general", which is what a teacher looking at one name needs."""
    contributions = {}
    for column, importance in zip(b["features"], b["importances"]):
        spread = b["feature_stds"].get(column) or 1.0
        z = abs(row[column] - b["feature_means"][column]) / spread
        contributions[column] = importance * z
    top = max(contributions, key=contributions.get)
    factor = b["friendly_names"][top]
    return factor, b["interventions"].get(factor)


def predict_many(records: List[StudentRecord], b: dict) -> List[Prediction]:
    """Score a whole batch with ONE call into the model.

    Calling the model once per row costs hundreds of times more than one call
    for the whole batch — every tree is walked with full Python/NumPy call
    overhead each time. On a free 0.1-CPU instance a 500-row batch took long
    enough that the platform killed the container mid-request, which is how
    this was found.

    The model predicts the change from the first-term grade. The expected
    final grade is that grade plus the change; the range and the chance of
    failing are read off the model's own out-of-fold errors, exactly as the
    training script measured them.
    """
    features = b["features"]
    prepared = [_prepare(rec, b) for rec in records]

    frame = pd.DataFrame(
        [[row[c] for c in features] for row, _ in prepared], columns=features
    )
    changes = b["model"].predict(frame)
    threshold = b["threshold"]
    residuals = b["residuals"]          # sorted
    low_off, high_off = b["interval_offsets"]
    passing = b["passing"]

    out = []
    for rec, (row, imputed), change in zip(records, prepared, changes):
        final = row["q1_term_grade"] + float(change)
        # Share of past errors that would carry this prediction below 75.
        fail = float(np.searchsorted(residuals, passing - final, side="left")
                     / len(residuals))
        is_high = fail >= threshold
        factor, action = _explain(row, b) if is_high else (None, None)
        out.append(
            Prediction(
                risk_score=round(fail, 4),
                risk_level="HIGH" if is_high else "LOW",
                predicted_final=_grade(final),
                range_low=_grade(final + low_off),
                range_high=_grade(final + high_off),
                primary_factor=factor,
                suggested_action=action,
                used_threshold=threshold,
                used_baseline_defaults=bool(imputed),
                imputed_fields=imputed,
                model_version=MODEL_VERSION,
                reference=rec.reference,
            )
        )
    return out


def _grade(value: float) -> float:
    """A grade on the scale a report card can show: the transmutation table
    bottoms out at 60 and nothing goes above 100. The chance of failing is
    computed before this, from the unclipped prediction."""
    return round(min(100.0, max(60.0, value)), 1)


def predict_one(rec: StudentRecord, b: dict) -> Prediction:
    """One student. Same code path as a batch of one, so the two endpoints can
    never drift apart."""
    return predict_many([rec], b)[0]


# ══════════════════════════════════════════════════════════════════════════════
# Routes
# ══════════════════════════════════════════════════════════════════════════════
@app.get("/", tags=["Meta"])
async def root():
    return {
        "service": "LBCA Academic Risk Classification",
        "model_version": MODEL_VERSION,
        "model_loaded": STATE["bundle"] is not None,
        "docs": "/docs",
    }


@app.get("/health", tags=["Meta"])
async def health():
    """Render polls this. 200 even when the model failed, so the platform can
    start the service and the error is visible rather than a restart loop."""
    return {
        "status": "ok" if STATE["bundle"] is not None else "degraded",
        "model_loaded": STATE["bundle"] is not None,
        "model_version": MODEL_VERSION,
        "error": STATE["error"],
    }


@app.get("/model-card", tags=["Meta"])
async def model_card():
    b = bundle()
    card = STATE["card"] or {}
    return {
        "model_version": MODEL_VERSION,
        # The deployed model may be a pipeline (scaler + estimator); name the
        # estimator, which is what the card describes.
        "algorithm": type(getattr(b["model"], "steps", [[None, b["model"]]])[-1][1]).__name__,
        "features": b["features"],
        "threshold": b["threshold"],
        "target": "Expected final grade, its likely range, and the chance it "
                  "falls below 75; HIGH when that chance is at least "
                  f"{round(b['threshold'] * 100)}%",
        "task": card.get("task"),
        # Written by the training script from the numbers it measured, so the
        # limitations can never describe a different model from the one loaded.
        "limitations": card.get("limitations") or [],
        "dataset": card.get("dataset"),
        # One flat set of figures each for the deployed model and the rule it
        # is measured against, which is the shape the app's card renders.
        "reported_metrics": _metrics_for(card, card.get("model")),
        "baseline_for_comparison": _metrics_for(
            card, "Baseline: Term 1 + average rise for the grade level"),
        "all_results": card.get("results"),
        "leave_one_school_out": card.get("leave_one_school_out"),
        "protocol": card.get("protocol"),
        "known_limits": card.get("limitations"),
    }


_CARD_NAMES = {"ExtraTreesRegressor": "Extra Trees",
               "RandomForestRegressor": "Random Forest",
               "GradientBoostingRegressor": "Gradient Boosting",
               "Ridge": "Ridge Regression"}
_SHOWN_METRICS = ("mae", "rmse", "within_1", "within_2", "range_coverage",
                  "fail_tp", "fail_fn", "fail_fp")


def _metrics_for(card: dict, name: Optional[str]) -> dict:
    """The headline figures for one row of the card's results table."""
    wanted = _CARD_NAMES.get(name, name)
    for row in card.get("results") or []:
        if row.get("model") == wanted:
            return {k: row[k] for k in _SHOWN_METRICS if k in row}
    return {}


@app.post("/predict", response_model=Prediction, tags=["Inference"],
          dependencies=[Depends(require_token)])
async def predict(record: StudentRecord):
    return predict_one(record, bundle())


@app.post("/predict/batch", response_model=BatchResponse, tags=["Inference"],
          dependencies=[Depends(require_token)])
async def predict_batch(payload: BatchRequest):
    if not payload.records:
        return BatchResponse(predictions=[], count=0)
    if len(payload.records) > MAX_BATCH:
        raise HTTPException(
            status_code=413,
            detail=f"Batch too large: {len(payload.records)} records, maximum {MAX_BATCH}.",
        )
    preds = predict_many(payload.records, bundle())
    return BatchResponse(predictions=preds, count=len(preds))


@app.post("/reload", tags=["Meta"], dependencies=[Depends(require_token)])
async def reload_model():
    """Re-read the model file without a redeploy — used after a retrain."""
    load_model()
    if STATE["bundle"] is None:
        raise HTTPException(status_code=500, detail=STATE["error"])
    return {"reloaded": True, "model_version": MODEL_VERSION}