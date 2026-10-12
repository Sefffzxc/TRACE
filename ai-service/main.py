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
The model estimates whether a learner will finish the term below 80 — the
mark at which DepEd records call for intervention — from the coursework
components of the class record, before the term exam is sat. It does NOT
predict the passing mark of 75: there were only 30 such cases in the
training records, too few to support a claim. It was validated on complete
coursework totals, so `/model-card` reports that limitation, and every
response carries `model_version` so a stored prediction can always be traced
back to the model that made it.

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
from pydantic import BaseModel, Field

MODEL_DIR = pathlib.Path(os.getenv("MODEL_DIR", "model"))
MODEL_PATH = MODEL_DIR / "risk_model_v2.joblib"
CARD_PATH = MODEL_DIR / "model_card_v2.json"
MODEL_VERSION = os.getenv("MODEL_VERSION", "v2.0.0-classrecord")
SERVICE_TOKEN = os.getenv("AI_SERVICE_TOKEN", "").strip()
MAX_BATCH = 500

STATE: dict = {"bundle": None, "card": None, "loaded_at": None, "error": None}


# ══════════════════════════════════════════════════════════════════════════════
# Model loading
# ══════════════════════════════════════════════════════════════════════════════
def load_model() -> None:
    try:
        bundle_ = joblib.load(MODEL_PATH)
        # `feature_importances_` on a RandomForest is a PROPERTY: every read
        # averages the importances of all 400 trees from scratch (~66 ms here,
        # far worse on a 0.1-CPU instance). It is read once per flagged student
        # to work out the primary factor, so on a batch it was costing more
        # than the predictions themselves. It cannot change after fitting, so
        # it is computed once, here.
        bundle_["importances"] = np.asarray(bundle_["model"].feature_importances_)
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
    """One learner, one subject, one term.

    These are the DepEd class-record figures a teacher has entered by the time
    a warning is useful: the two coursework components, raw, as a percentage
    and weighted. The summative tests and the term examination are
    deliberately absent — they are sat at the end of the term, and a model
    that needed them would be reporting a result rather than anticipating
    one.

    Any field may be omitted; the service substitutes the training median and
    says so in the response.
    """

    ww_total: Optional[float] = Field(default=None, ge=0,
        description="Written Works raw total for the term")
    # Not capped at 100. Thirteen rows of the training data score above the
    # highest possible score, because a teacher awarded a bonus; capping here
    # would reject a figure the class record itself produced.
    ww_pct: Optional[float] = Field(default=None, ge=0, le=200,
        description="Written Works percentage score (total / highest possible x 100)")
    ww_weighted: Optional[float] = Field(default=None, ge=0, le=100,
        description="Written Works weighted score (percentage x component weight)")
    pt_total: Optional[float] = Field(default=None, ge=0,
        description="Performance Task raw total for the term")
    pt_pct: Optional[float] = Field(default=None, ge=0, le=200,
        description="Performance Task percentage score")
    pt_weighted: Optional[float] = Field(default=None, ge=0, le=100,
        description="Performance Task weighted score")
    pre_exam_weighted: Optional[float] = Field(default=None, ge=0, le=100,
        description="Written Works weighted + Performance Task weighted: what "
                    "the learner has banked before the term examination")
    component_gap: Optional[float] = Field(default=None, ge=0, le=200,
        description="Absolute difference between the two percentage scores")
    grade_level: Optional[float] = Field(default=None, ge=1, le=12)
    quarter: Optional[float] = Field(default=None, ge=1, le=4)

    # Echoed back untouched so the caller can match responses to its own rows.
    reference: Optional[str] = Field(default=None, max_length=120)


class Prediction(BaseModel):
    risk_score: float = Field(description="Probability of ending the term below 80, 0-1")
    risk_level: str = Field(description="HIGH or LOW, using the service threshold")
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


# Request field -> training column, in the order the model expects.
FIELD_MAP = {
    "ww_total": "ww_total",
    "ww_pct": "ww_pct",
    "ww_weighted": "ww_weighted",
    "pt_total": "pt_total",
    "pt_pct": "pt_pct",
    "pt_weighted": "pt_weighted",
    "pre_exam_weighted": "pre_exam_weighted",
    "component_gap": "component_gap",
    "grade_level": "grade_level",
    "quarter": "quarter",
}


# ══════════════════════════════════════════════════════════════════════════════
# Inference
# ══════════════════════════════════════════════════════════════════════════════
def _prepare(rec: StudentRecord, b: dict) -> tuple:
    """Turn one request into the model's row, noting what had to be imputed."""
    medians = b["medians"]
    row, imputed = {}, []
    for field, column in FIELD_MAP.items():
        value = getattr(rec, field)
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

    Calling predict_proba once per row costs ~370x more than one call for the
    whole batch — 400 trees are walked with full Python/NumPy call overhead
    every time. On a free 0.1-CPU instance a 500-row batch took long enough
    that the platform killed the container mid-request, which is how this was
    found.

    This is also the form the audit used: final_pipeline.py evaluates with
    `model.predict_proba(Xte)` over the whole test matrix, so batching matches
    the measured results rather than departing from them. Batched and per-row
    floats can differ by one ULP (~2e-16) through summation order; measured
    over 20,000 rows that never changed a stored risk_score at 4 decimals nor
    a HIGH/LOW decision, and the smallest gap between distinct probabilities
    the model produces is 2.7e-9 — seven orders of magnitude wider than the
    perturbation.
    """
    features = b["features"]
    prepared = [_prepare(rec, b) for rec in records]

    frame = pd.DataFrame(
        [[row[c] for c in features] for row, _ in prepared], columns=features
    )
    probas = b["model"].predict_proba(frame)[:, 1]
    threshold = b["threshold"]

    out = []
    for rec, (row, imputed), proba in zip(records, prepared, probas):
        proba = float(proba)
        is_high = proba >= threshold
        factor, action = _explain(row, b) if is_high else (None, None)
        out.append(
            Prediction(
                risk_score=round(proba, 4),
                risk_level="HIGH" if is_high else "LOW",
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
        "algorithm": type(b["model"]).__name__,
        "features": b["features"],
        "threshold": b["threshold"],
        "target": "HIGH if the term grade will fall below 80",
        "limitations": [
            "The term grade is a weighted sum of the components the model "
            "reads, so the task is substantially arithmetic. A single cut on "
            "the pre-examination weighted total reaches macro F1 0.864 on the "
            "same data; the model's advantage over it was not statistically "
            "significant.",
            "Measured at the point where written works and performance tasks "
            "are complete and the term examination is not. No claim is made "
            "about earlier points in the term.",
            "Does not transfer across cohorts without threshold "
            "recalibration: carrying the earlier cohort's threshold to an "
            "unseen one dropped recall from 0.835 to 0.645.",
            "Trained on Grade 8 Filipino records (86.8% and 95.6% of the "
            "training rows). Conclusions do not extend to other grade levels "
            "or subjects.",
            "The training export carries no attendance column, so attendance "
            "plays no part in this model.",
        ],
        # Keyed to the card the current training script writes. These were
        # reading key names from the previous model and silently returning
        # null for all three.
        "dataset": card.get("dataset"),
        "reported_metrics": card.get("results"),
        "temporal_validation": card.get("temporal_validation"),
        "protocol": card.get("protocol"),
        "known_limits": card.get("limitations"),
    }


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