"""The bridge between this API and the risk-classification service.

Design rules, in order of importance:

1. **Saving data must never fail because the model is down.** Every call is
   wrapped, the timeout is short, and a failure produces a rule-based result
   instead of an error. A teacher encoding a class record should not know or
   care whether the model answered.

2. **A fallback is never dressed up as a prediction.** When the service cannot
   be reached the returned row carries ``source="fallback"``, the reason in
   ``error``, and the rule engine's own score. The API returns it and the UI
   labels it. Nothing pretends the model spoke.

3. **Missing inputs are not invented here.** If the teacher has not encoded
   Performance Tasks yet, the field is sent as ``null``. The AI service
   substitutes its *training* median — a documented, reproducible value — and
   reports which fields it filled in. This module never makes up a number.

4. **Every prediction is reproducible.** The exact payload sent is stored
   alongside the answer, so any figure shown in the system can be recomputed by
   hand or re-run against a later model.

Why HTTP and not an in-process ``joblib.load``: the model is a separate
deployable so it can be retrained and redeployed without touching the
monitoring system, and so the backend does not have to pin scikit-learn,
numpy and pandas — which also keeps the backend's own container small enough
for a free Render instance.
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

import httpx

from app.core.config import settings
from app.services import risk

# Request field -> the dataset column it stands for. This mirrors FIELD_MAP in
# ai-service/main.py; the two must be changed together.
MODEL_FIELDS = (
    "ww_total",
    "ww_pct",
    "ww_weighted",
    "pt_total",
    "pt_pct",
    "pt_weighted",
    "pre_exam_weighted",
    "component_gap",
    "grade_level",
    "quarter",
)

# The subset that comes from the learner's own row of the class record. The
# grade level comes from the enrolment and the quarter from the record being
# encoded, so neither belongs in a loop over a learner's cells.
ENCODED_FIELDS = (
    "ww_total",
    "ww_pct",
    "ww_weighted",
    "pt_total",
    "pt_pct",
    "pt_weighted",
    "pre_exam_weighted",
    "component_gap",
)

# How the rule engine's four levels collapse onto the model's two. The model
# answers one question — "is this learner heading for a term grade below 80" —
# so a fallback has to answer the same question, not a different one.
_FALLBACK_HIGH_LEVELS = {"critical", "high"}


@dataclass
class PredictionResult:
    """What gets written to ``ai_risk_predictions``."""

    risk_score: float = 0.0            # probability 0–1
    risk_level: str = "LOW"            # HIGH | LOW
    used_threshold: float = 0.5
    primary_factor: Optional[str] = None
    suggested_action: Optional[str] = None
    source: str = "model"              # model | fallback
    model_version: str = "unknown"
    used_baseline_defaults: bool = False
    imputed_fields: List[str] = field(default_factory=list)
    features: Dict[str, Any] = field(default_factory=dict)
    latency_ms: Optional[int] = None
    error: Optional[str] = None

    def as_columns(self) -> dict:
        """Shape it for the ORM model (JSON columns serialised)."""
        return {
            "risk_score": self.risk_score,
            "risk_level": self.risk_level,
            "used_threshold": self.used_threshold,
            "primary_factor": self.primary_factor,
            "suggested_action": self.suggested_action,
            "source": self.source,
            "model_version": self.model_version,
            "used_baseline_defaults": self.used_baseline_defaults,
            "imputed_fields": json.dumps(self.imputed_fields),
            "features": json.dumps(self.features),
            "latency_ms": self.latency_ms,
            "error": self.error,
        }


# ══════════════════════════════════════════════════════════════════════════════
# Building the model's input from a PACE row
# ══════════════════════════════════════════════════════════════════════════════
def grade_level_number(label: Optional[str]) -> Optional[float]:
    """"Grade 8" / "8" -> 8.0.  Anything unreadable -> None (the service
    imputes and says so, rather than this code guessing)."""
    if not label:
        return None
    digits = "".join(ch for ch in str(label) if ch.isdigit())
    if not digits:
        return None
    try:
        value = float(digits)
    except ValueError:
        return None
    return value if 1 <= value <= 12 else None


# The component weights the model was TRAINED under. The records it learned
# from came from class records weighted 30/50/20 and 40/40/20; the MATATAG
# template this system now uses runs 20/50/30 for the core subjects and
# 20/60/20 for MAPEH.
#
# A weighted score is just a percentage multiplied by a constant, so feeding
# the model figures scaled by the new weights would hand it a feature on a
# different scale from everything it was fitted on — silently, with no error
# and a plausible-looking number coming back. The percentages themselves are
# weight-free and are passed through untouched; the weighted features are
# rebuilt here at the training scale so the model sees the distribution it
# was fitted on, whatever a particular school's sheet is set to.
#
# This keeps today's predictions honest. It is not a substitute for
# retraining: the target was also derived through the old transmutation
# table, under which a term grade of 80 began at an initial grade of 68.00,
# where the MATATAG table puts it at 75.90. Once there are records under the
# new template the model should be refitted on them.
TRAINING_WEIGHTS = {"written": 30.0, "performance": 50.0}


def features_from_record(record, grade_level_label: Optional[str] = None,
                         quarter: Optional[int] = None,
                         weights: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Read the model's ten inputs off a class-record row.

    The percentages come straight off the stored record, so the model reads
    the same numbers the teacher does. The weighted figures are recomputed at
    the training scale — see TRAINING_WEIGHTS above for why.

    The examinations are excluded deliberately: both summative tests and the
    term examination are the event the warning is meant to precede, and a
    model given them would be restating a result the teacher already has
    rather than anticipating one. The initial and term grades are excluded
    twice over — they are the outcome, and they are arithmetic on
    examinations the model may not see.

    Cells the teacher has not filled in stay ``None``. The service
    substitutes the training median for those and reports which ones it
    substituted, so a thin record never passes for a confident reading.

    ``weights`` is accepted for callers that still pass it, and ignored.
    """
    ww_pct = _num(getattr(record, "ww_percent", None))
    pt_pct = _num(getattr(record, "pt_percent", None))

    def at_training_scale(percentage, key):
        if percentage is None:
            return None
        return round(percentage * TRAINING_WEIGHTS[key] / 100.0, 2)

    ww_weighted = at_training_scale(ww_pct, "written")
    pt_weighted = at_training_scale(pt_pct, "performance")

    # Both derived figures need both components. Computing either from one
    # half would quietly treat an un-entered component as a zero, which is
    # the defect that made 65 rows of the training export look like failures.
    pre_exam = (round(ww_weighted + pt_weighted, 2)
                if ww_weighted is not None and pt_weighted is not None else None)
    gap = (round(abs(ww_pct - pt_pct), 2)
           if ww_pct is not None and pt_pct is not None else None)

    return {
        "ww_total": _num(getattr(record, "ww_total_raw", None)),
        "ww_pct": ww_pct,
        "ww_weighted": ww_weighted,
        "pt_total": _num(getattr(record, "pt_total_raw", None)),
        "pt_pct": pt_pct,
        "pt_weighted": pt_weighted,
        "pre_exam_weighted": pre_exam,
        "component_gap": gap,
        "grade_level": grade_level_number(grade_level_label),
        "quarter": _num(quarter if quarter is not None
                        else getattr(record, "grading_period", None)),
    }


# The previous name, kept so existing callers keep working while the class
# record screens are migrated.
features_from_pace = features_from_record


def _fraction(value) -> Optional[float]:
    """Accept a weight as either 30 or 0.30 and return it as a fraction."""
    number = _num(value)
    if number is None:
        return None
    if number > 1.0:
        number = number / 100.0
    return round(number, 4) if 0.0 <= number <= 1.0 else None


def _num(value) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def coverage(features: Dict[str, Any]) -> float:
    """Fraction of the model's inputs that were actually encoded (0–1)."""
    present = sum(1 for key in MODEL_FIELDS if features.get(key) is not None)
    return round(present / len(MODEL_FIELDS), 3)


# ══════════════════════════════════════════════════════════════════════════════
# The rule-based fallback
# ══════════════════════════════════════════════════════════════════════════════
# Where the arithmetic rule puts the line, fitted on the 1,094 usable rows of
# the merged class-record export: flag a learner whose weighted coursework
# total before the term examination falls below this. On that data it reaches
# macro F1 0.864 with recall 0.814 and precision 0.730 — close enough to the
# model that it is the honest comparator, and good enough to stand in when the
# model cannot be reached.
ARITHMETIC_CUT = 56.6


def fallback_prediction(pace, reason: str, features: Dict[str, Any]) -> PredictionResult:
    """What to store when the model could not be reached.

    Two different rules, because the system serves two kinds of school. A
    school keeping a DepEd class record is judged on the coursework it has
    actually banked; a school tracking PACE booklets has no such figure and
    falls back to the booklet rule instead.

    Neither score is a probability and neither must be read as one — which is
    why ``source`` says "fallback" and the interface shows a different badge.
    """
    pre_exam = features.get("pre_exam_weighted")
    if pre_exam is not None:
        at_risk = pre_exam < ARITHMETIC_CUT
        # Distance from the line, squashed into 0–1 so the badge can sort by
        # it. It is a rule's confidence, not the model's probability.
        margin = min(1.0, abs(pre_exam - ARITHMETIC_CUT) / ARITHMETIC_CUT)
        return PredictionResult(
            risk_score=round(0.5 + (margin / 2 if at_risk else -margin / 2), 4),
            risk_level="HIGH" if at_risk else "LOW",
            used_threshold=ARITHMETIC_CUT,
            primary_factor=("weighted coursework before the term exam "
                            "(rule-based)" if at_risk else None),
            suggested_action=(
                "Coursework banked so far is below the level that usually ends "
                "the term under 80. Review written works and performance tasks "
                "with the learner before the term examination."
                if at_risk else None),
            source="fallback",
            model_version="rule-based (arithmetic cut)",
            used_baseline_defaults=False,
            imputed_fields=[],
            features=features,
            error=reason,
        )

    evaluation = risk.evaluate(
        pace.current_pace, pace.expected_pace, pace.total_paces
    )
    level = evaluation["risk_level"]

    # Name the rule component that dominated, so the fallback is at least as
    # explainable as the model it stood in for.
    if evaluation["paces_behind"] >= 1:
        factor = "PACE booklets behind (rule-based)"
    elif evaluation["pace_percent"] < 100.0:
        factor = "curriculum completion (rule-based)"
    else:
        factor = None

    return PredictionResult(
        risk_score=round(evaluation["risk_score"] / 100.0, 4),
        risk_level="HIGH" if level in _FALLBACK_HIGH_LEVELS else "LOW",
        used_threshold=risk.HIGH_AT / 100.0,
        primary_factor=factor if level in _FALLBACK_HIGH_LEVELS else None,
        suggested_action=evaluation["intervention"],
        source="fallback",
        model_version="rule-based",
        used_baseline_defaults=False,
        imputed_fields=[],
        features=features,
        error=reason,
    )


# ══════════════════════════════════════════════════════════════════════════════
# Talking to the service
# ══════════════════════════════════════════════════════════════════════════════
def _headers() -> dict:
    if settings.AI_SERVICE_TOKEN:
        return {"X-Service-Token": settings.AI_SERVICE_TOKEN}
    return {}


def _url(path: str) -> str:
    return f"{settings.AI_SERVICE_URL.rstrip('/')}{path}"


# A free Render instance stops after 15 idle minutes, and the request that
# arrives next is what starts it again. While the container boots — importing
# scikit-learn on a 0.1-CPU instance is most of it — Render's proxy answers
# 502/503 rather than holding the request open. So the very first attempt after
# a quiet period is *expected* to fail, and reporting "the AI service is
# unreachable" on the strength of it is wrong: the service is starting up
# precisely because we asked.
COLD_START_STATUSES = {502, 503, 504}
WAKE_ATTEMPTS = 4
WAKE_RETRY_PAUSE = 15.0


async def _send_with_wake(
    method: str,
    path: str,
    *,
    json_body: Optional[dict] = None,
    timeout: Optional[float] = None,
) -> httpx.Response:
    """Send a request, tolerating a cold start. Only the paths where waiting is
    acceptable use this — the inline save never does, because a teacher's save
    must not sit behind a container boot.

    An authentication or validation failure is NOT a cold start and is raised on
    the first attempt, so a wrong token still surfaces immediately instead of
    being retried for a minute and then reported as a timeout.
    """
    timeout = timeout or settings.AI_WAKE_TIMEOUT_SECONDS
    for attempt in range(WAKE_ATTEMPTS):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.request(
                    method, _url(path), json=json_body, headers=_headers()
                )
                response.raise_for_status()
                return response
        except Exception as exc:  # noqa: BLE001
            waking = isinstance(exc, (httpx.TimeoutException, httpx.ConnectError)) or (
                isinstance(exc, httpx.HTTPStatusError)
                and exc.response.status_code in COLD_START_STATUSES
            )
            if not waking or attempt == WAKE_ATTEMPTS - 1:
                raise
            await asyncio.sleep(WAKE_RETRY_PAUSE)
    raise RuntimeError("unreachable")  # pragma: no cover


def _from_payload(payload: dict, features: Dict[str, Any], latency_ms: int) -> PredictionResult:
    return PredictionResult(
        risk_score=float(payload.get("risk_score", 0.0)),
        risk_level=str(payload.get("risk_level", "LOW")).upper(),
        used_threshold=float(payload.get("used_threshold", 0.5)),
        primary_factor=payload.get("primary_factor"),
        suggested_action=payload.get("suggested_action"),
        source="model",
        model_version=str(payload.get("model_version", "unknown")),
        used_baseline_defaults=bool(payload.get("used_baseline_defaults", False)),
        imputed_fields=list(payload.get("imputed_fields") or []),
        features=features,
        latency_ms=latency_ms,
    )


async def predict(pace, grade_level_label: Optional[str] = None,
                  timeout: Optional[float] = None) -> PredictionResult:
    """Score one PACE row. Never raises."""
    features = features_from_pace(pace, grade_level_label)

    if not settings.AI_ENABLED:
        return fallback_prediction(pace, "AI_ENABLED is off.", features)

    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(
            timeout=timeout or settings.AI_TIMEOUT_SECONDS
        ) as client:
            response = await client.post(
                _url("/predict"), json=features, headers=_headers()
            )
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:  # noqa: BLE001 — any failure means "use the rules"
        return fallback_prediction(pace, _describe(exc), features)

    elapsed = int((time.perf_counter() - started) * 1000)
    return _from_payload(payload, features, elapsed)


async def predict_many(
    rows: Sequence[tuple],
    timeout: Optional[float] = None,
) -> List[PredictionResult]:
    """Score many PACE rows in one request.

    ``rows`` is a sequence of ``(pace_row, grade_level_label)`` pairs. The order
    of the results matches the order of the input. If the batch call fails,
    *every* row comes back as a fallback — a partial mix would be harder to
    interpret than a clean "the model was unavailable for this run".
    """
    prepared = [(pace, features_from_pace(pace, label)) for pace, label in rows]
    if not prepared:
        return []

    if not settings.AI_ENABLED:
        return [fallback_prediction(p, "AI_ENABLED is off.", f) for p, f in prepared]

    payload = {
        "records": [
            {**feats, "reference": str(getattr(pace, "pace_id", index))}
            for index, (pace, feats) in enumerate(prepared)
        ]
    }

    started = time.perf_counter()
    try:
        response = await _send_with_wake(
            "POST", "/predict/batch", json_body=payload, timeout=timeout
        )
        body = response.json()
        predictions = body["predictions"]
        if len(predictions) != len(prepared):
            raise ValueError(
                f"service returned {len(predictions)} predictions "
                f"for {len(prepared)} records"
            )
    except Exception as exc:  # noqa: BLE001
        reason = _describe(exc, timeout or settings.AI_WAKE_TIMEOUT_SECONDS)
        return [fallback_prediction(p, reason, f) for p, f in prepared]

    elapsed = int((time.perf_counter() - started) * 1000)
    per_row = max(1, elapsed // len(prepared))
    return [
        _from_payload(item, feats, per_row)
        for item, (_, feats) in zip(predictions, prepared)
    ]


async def service_status(timeout: Optional[float] = None) -> dict:
    """Ask the AI service how it is. Also serves as the warm-up ping: on a free
    Render instance the first call wakes the container, which is why the
    default timeout here is the long one."""
    if not settings.AI_ENABLED:
        return {
            "enabled": False,
            "reachable": False,
            "service_url": settings.AI_SERVICE_URL,
            "detail": "Disabled by configuration (AI_ENABLED=false).",
        }

    started = time.perf_counter()
    try:
        response = await _send_with_wake("GET", "/health", timeout=timeout)
        body = response.json()
    except Exception as exc:  # noqa: BLE001
        return {
            "enabled": True,
            "reachable": False,
            "service_url": settings.AI_SERVICE_URL,
            "detail": _describe(exc, timeout or settings.AI_WAKE_TIMEOUT_SECONDS),
        }

    return {
        "enabled": True,
        "reachable": True,
        "service_url": settings.AI_SERVICE_URL,
        "model_version": body.get("model_version"),
        "model_loaded": bool(body.get("model_loaded")),
        "detail": body.get("error"),
        "latency_ms": int((time.perf_counter() - started) * 1000),
    }


async def model_card(timeout: Optional[float] = None) -> Optional[dict]:
    """Fetch the model card — metrics, features, threshold and the documented
    limitations. The UI shows these next to any prediction so the stated
    limits travel with the number."""
    if not settings.AI_ENABLED:
        return None
    try:
        response = await _send_with_wake("GET", "/model-card", timeout=timeout)
        return response.json()
    except Exception:  # noqa: BLE001
        return None


def _describe(exc: Exception, timeout: Optional[float] = None) -> str:
    """A short, honest reason string for the ``error`` column.

    ``timeout`` is the limit that actually applied to the call, so a message
    never quotes a number the request was not held to.
    """
    limit = settings.AI_TIMEOUT_SECONDS if timeout is None else timeout
    if isinstance(exc, httpx.TimeoutException):
        return (
            f"AI service did not answer within {limit:g}s "
            f"(it may be waking from idle)."
        )
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code in COLD_START_STATUSES:
            return (
                f"AI service returned HTTP {code} on every one of "
                f"{WAKE_ATTEMPTS} attempts over "
                f"{WAKE_RETRY_PAUSE * (WAKE_ATTEMPTS - 1):g}s — it is either "
                f"still starting up or failing to start. Check its logs."
            )
        if code == 401:
            return (
                "AI service rejected the token (HTTP 401). AI_SERVICE_TOKEN "
                "does not match the value set on the model service."
            )
        return f"AI service returned HTTP {code}."
    if isinstance(exc, httpx.ConnectError):
        return f"Could not connect to the AI service at {settings.AI_SERVICE_URL}."
    return f"{type(exc).__name__}: {exc}"
