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
The model estimates END-OF-QUARTER risk (a quarterly grade below 75, or no
grade recorded) from attendance and assessment records. It was validated on
quarter-end values only, so `/model-card` reports that limitation and every
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
MODEL_VERSION = os.getenv("MODEL_VERSION", "v2-2026-09")
SERVICE_TOKEN = os.getenv("AI_SERVICE_TOKEN", "").strip()
MAX_BATCH = 500

STATE: dict = {"bundle": None, "card": None, "loaded_at": None, "error": None}


# ══════════════════════════════════════════════════════════════════════════════
# Model loading
# ══════════════════════════════════════════════════════════════════════════════
def load_model() -> None:
    try:
        STATE["bundle"] = joblib.load(MODEL_PATH)
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
    """One student-subject-quarter. Any field may be omitted; the service
    substitutes the training median and reports that it did so."""

    absences: Optional[float] = Field(default=None, ge=0, description="No of Absences")
    ww_total_raw: Optional[float] = Field(default=None, ge=0, description="WW Total (Raw)")
    ww_percent: Optional[float] = Field(default=None, ge=0, le=100, description="WW % Score")
    pt_total_raw: Optional[float] = Field(default=None, ge=0, description="PT Total (Raw)")
    pt_percent: Optional[float] = Field(default=None, ge=0, le=100, description="PT % Score")
    missed_ww_items: Optional[float] = Field(default=None, ge=0)
    missed_pt_items: Optional[float] = Field(default=None, ge=0)
    extracurricular_count: Optional[float] = Field(default=None, ge=0)
    grade_level: Optional[float] = Field(default=None, ge=1, le=12)

    # Echoed back untouched so the caller can match responses to its own rows.
    reference: Optional[str] = Field(default=None, max_length=120)


class Prediction(BaseModel):
    risk_score: float = Field(description="Probability of end-of-quarter failure, 0-1")
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


# Request field -> dataset column, in the order the model expects.
FIELD_MAP = {
    "absences": "No of Absences",
    "ww_total_raw": "WW Total (Raw)",
    "ww_percent": "WW % Score",
    "pt_total_raw": "PT Total (Raw)",
    "pt_percent": "PT % Score",
    "missed_ww_items": "Missed WW Items",
    "missed_pt_items": "Missed PT Items",
    "extracurricular_count": "Extracurricular Activities",
    "grade_level": "Grade Level",
}


# ══════════════════════════════════════════════════════════════════════════════
# Inference
# ══════════════════════════════════════════════════════════════════════════════
def predict_one(rec: StudentRecord, b: dict) -> Prediction:
    features = b["features"]
    medians = b["medians"]

    row, imputed = {}, []
    for field, column in FIELD_MAP.items():
        value = getattr(rec, field)
        if value is None:
            row[column] = medians[column]
            imputed.append(column)
        else:
            row[column] = float(value)

    frame = pd.DataFrame([[row[c] for c in features]], columns=features)
    proba = float(b["model"].predict_proba(frame)[0, 1])
    is_high = proba >= b["threshold"]

    factor = None
    action = None
    if is_high:
        # The feature furthest from the cohort mean, weighted by its importance.
        # This answers "why this student" rather than "what matters in general".
        contributions = {}
        for column, importance in zip(features, b["model"].feature_importances_):
            spread = b["feature_stds"].get(column) or 1.0
            z = abs(row[column] - b["feature_means"][column]) / spread
            contributions[column] = importance * z
        top = max(contributions, key=contributions.get)
        factor = b["friendly_names"][top]
        action = b["interventions"].get(factor)

    return Prediction(
        risk_score=round(proba, 4),
        risk_level="HIGH" if is_high else "LOW",
        primary_factor=factor,
        suggested_action=action,
        used_threshold=b["threshold"],
        used_baseline_defaults=bool(imputed),
        imputed_fields=imputed,
        model_version=MODEL_VERSION,
        reference=rec.reference,
    )


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
        "target": "HIGH if end-of-quarter grade < 75, or no grade recorded",
        "limitations": [
            "Validated on quarter-end feature values only; mid-quarter performance "
            "has not been measured and must not be claimed.",
            "Does not transfer across school years without threshold recalibration "
            "(recall fell to 0.573 on an unseen cohort).",
            "Validated on Grade 8 Filipino records only (88.3% and 95.5% of the "
            "training data respectively).",
            "Attendance dominates: attendance alone reaches 97% of the full model's "
            "macro F1.",
        ],
        "reported_metrics": card.get("test_results_10_splits_mean"),
        "baseline_for_comparison": card.get("baseline"),
        "protocol": card.get("protocol"),
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
    b = bundle()
    preds = [predict_one(r, b) for r in payload.records]
    return BatchResponse(predictions=preds, count=len(preds))


@app.post("/reload", tags=["Meta"], dependencies=[Depends(require_token)])
async def reload_model():
    """Re-read the model file without a redeploy — used after a retrain."""
    load_model()
    if STATE["bundle"] is None:
        raise HTTPException(status_code=500, detail=STATE["error"])
    return {"reloaded": True, "model_version": MODEL_VERSION}
