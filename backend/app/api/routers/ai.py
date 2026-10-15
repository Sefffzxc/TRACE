"""Read and control endpoints for the machine-learning risk classifier.

The model itself lives in a separate service (``../ai-service``). Nothing here
loads a model file — this router reads the predictions that were logged when
records were saved, and forwards three things to the model service: a health
check, the model card, and an explicit re-score.

Two ideas run through all of it:

* **A prediction is evidence, not a verdict.** Every response says which model
  version produced it, on what input, and whether any input was missing. The
  early-warning list the school acts on is still the rule-based one.
* **Nothing hides a fallback.** If the model was unreachable the row says
  ``source="fallback"`` and carries the reason.
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, school_scope
from app.api.scoping import get_scoped, scoped
from app.core.config import settings
from app.db.session import get_db
from app.models.staff import ROLE_ADMIN, Staff
from app.models.students import (
    AIRiskPrediction,
    Student,
    StudentEnrollment,
    StudentPace,
)
from app.schemas.students import (
    AIPredictionResponse,
    AIStatusResponse,
    RescoreRequest,
    RescoreResponse,
)
from app.services import ml_client
from app.services.enrichment import (Lookups, is_year_prediction,
                                     newest_year_predictions,
                                     sections_for_teacher)

router = APIRouter(prefix="/api/ai", tags=["AI risk classification"])


# ══════════════════════════════════════════════════════════════════════════════
# Service meta
# ══════════════════════════════════════════════════════════════════════════════
@router.get("/status", response_model=AIStatusResponse)
async def ai_status(
    wake: bool = Query(
        default=True,
        description="Wait long enough for an idle service to start (free hosting "
                    "tiers sleep after 15 minutes).",
    ),
    # Deliberately NOT scoped to a school, and the only kind of endpoint on
    # this router that is not: it reports whether a shared service is running.
    # There is no student, no school and no record in the answer, so there is
    # nothing here to keep inside a tenant — and knowing whether the deployment
    # is healthy is precisely the system operator's job.
    _: Staff = Depends(get_current_user),
):
    """Is the classifier up? The UI calls this on load, which doubles as the
    warm-up ping so the first real save does not land on a sleeping service."""
    timeout = settings.AI_WAKE_TIMEOUT_SECONDS if wake else settings.AI_TIMEOUT_SECONDS
    return AIStatusResponse(**await ml_client.service_status(timeout=timeout))


@router.get("/model-card")
async def ai_model_card(_: Staff = Depends(get_current_user)):
    """The model's own description of itself: algorithm, features, threshold,
    measured metrics and — deliberately not optional — its limitations.

    The UI shows these beside the predictions. A number without its limits is
    the thing this project is trying not to produce.

    Unscoped for the same reason as `/status`: this describes the model, not
    anybody's students. The metrics in it come from the published evaluation,
    not from any school's records.
    """
    card = await ml_client.model_card()
    if card is None:
        raise HTTPException(
            status_code=503,
            detail="The AI service is not reachable, so its model card cannot be read.",
        )
    return card


# ══════════════════════════════════════════════════════════════════════════════
# Logged predictions
# ══════════════════════════════════════════════════════════════════════════════
@router.get("/predictions", response_model=List[AIPredictionResponse])
async def list_predictions(
    student_id: Optional[int] = None,
    section_id: Optional[int] = None,
    subject: Optional[str] = None,
    risk_level: Optional[str] = Query(default=None, description="HIGH or LOW"),
    source: Optional[str] = Query(default=None, description="model or fallback"),
    latest_only: bool = Query(
        default=True,
        description="One row per learner and subject (the newest year-model "
                    "answer). False returns the "
                    "full prediction history.",
    ),
    limit: int = Query(default=500, ge=1, le=2000),
    mine: bool = Query(default=False),
    current: Staff = Depends(get_current_user),
    scope: int = Depends(school_scope),
    db: AsyncSession = Depends(get_db),
):
    stmt = scoped(
        select(AIRiskPrediction).order_by(AIRiskPrediction.prediction_id.desc()),
        AIRiskPrediction,
        scope,
    )
    if student_id:
        stmt = stmt.where(AIRiskPrediction.student_id == student_id)
    if subject:
        stmt = stmt.where(AIRiskPrediction.subject == subject)
    if risk_level:
        stmt = stmt.where(AIRiskPrediction.risk_level == risk_level.upper())
    if source:
        stmt = stmt.where(AIRiskPrediction.source == source.lower())
    rows = (await db.execute(stmt)).scalars().all()

    lookups = await Lookups.load(db, school_id=scope)
    students = {
        s.student_id: s
        for s in (
            await db.execute(scoped(select(Student), Student, scope))
        ).scalars()
    }
    enrollments = {
        e.enrollment_id: e
        for e in (
            await db.execute(
                scoped(select(StudentEnrollment), StudentEnrollment, scope)
            )
        ).scalars()
    }

    allowed: Optional[set[int]] = None
    if mine and current.role != ROLE_ADMIN:
        allowed = set(await sections_for_teacher(db, current.id, school_id=scope))

    seen: set = set()
    out: List[AIPredictionResponse] = []
    for row in rows:  # newest first, so the first row per key is the current one
        if latest_only:
            # One year-model answer per learner and subject; predictions from
            # the earlier per-term model are history, not current.
            if not is_year_prediction(row):
                continue
            key = (row.student_id, row.enrollment_id, row.subject)
            if key in seen:
                continue
            seen.add(key)

        enrollment = enrollments.get(row.enrollment_id)
        if section_id and (enrollment is None or enrollment.section_id != section_id):
            continue
        if allowed is not None and (
            enrollment is None or enrollment.section_id not in allowed
        ):
            continue

        item = AIPredictionResponse.model_validate(row)
        student = students.get(row.student_id)
        item.student_name = student.full_name if student else None
        if enrollment is not None:
            item.section_name = lookups.sections.get(enrollment.section_id)
            item.grade_level_name = lookups.grade_levels.get(enrollment.grade_level_id)
        out.append(item)
        if len(out) >= limit:
            break
    return out


@router.get("/predictions/{prediction_id}", response_model=AIPredictionResponse)
async def get_prediction(
    prediction_id: int,
    _: Staff = Depends(get_current_user),
    scope: int = Depends(school_scope),
    db: AsyncSession = Depends(get_db),
):
    """One prediction with the exact input that produced it — the "show me why"
    view, and what makes any number on screen checkable by hand."""
    row = await get_scoped(db, AIRiskPrediction, prediction_id, scope, "Prediction")
    return AIPredictionResponse.model_validate(row)


@router.get("/summary")
async def prediction_summary(
    _: Staff = Depends(get_current_user),
    scope: int = Depends(school_scope),
    db: AsyncSession = Depends(get_db),
):
    """Counts for the dashboard, over the newest prediction per PACE record.

    ``fallback`` and ``incomplete_input`` are reported next to the HIGH/LOW
    counts on purpose: a dashboard that shows "12 students at risk" without
    saying that four of those came from the fallback, or that six were scored
    with missing assessment data, is overstating what the model knows.
    """
    rows = (
        await db.execute(
            scoped(
                select(AIRiskPrediction).order_by(AIRiskPrediction.prediction_id),
                AIRiskPrediction,
                scope,
            )
        )
    ).scalars().all()

    current = list(newest_year_predictions(rows).values())

    high = [r for r in current if r.risk_level == "HIGH" and r.source == "model"]
    return {
        "total_scored": len(current),
        "high": sum(1 for r in current if r.risk_level == "HIGH"),
        "low": sum(1 for r in current if r.risk_level == "LOW"),
        "from_model": sum(1 for r in current if r.source == "model"),
        "from_fallback": sum(1 for r in current if r.source == "fallback"),
        "incomplete_input": sum(1 for r in current if r.used_baseline_defaults),
        "students_high": len({r.student_id for r in high}),
        "model_versions": sorted(
            {r.model_version for r in current if r.source == "model"}
        ),
        "top_factors": _count_factors(high),
    }


def _count_factors(rows: list) -> list:
    counts: dict = {}
    for row in rows:
        if row.primary_factor:
            counts[row.primary_factor] = counts.get(row.primary_factor, 0) + 1
    return [
        {"factor": name, "count": count}
        for name, count in sorted(counts.items(), key=lambda kv: -kv[1])
    ]


# ══════════════════════════════════════════════════════════════════════════════
# Re-scoring
# ══════════════════════════════════════════════════════════════════════════════
@router.post("/rescore", response_model=RescoreResponse)
async def rescore(
    payload: RescoreRequest,
    current: Staff = Depends(get_current_user),
    scope: int = Depends(school_scope),
    db: AsyncSession = Depends(get_db),
):
    """Run the classifier over stored PACE records again.

    Used after encoding assessment data that was missing, or after the model is
    retrained and redeployed. It appends new prediction rows; the earlier ones
    stay, so "what did the model say before we filled in the Performance Tasks"
    is always answerable.
    """
    # The model reads a learner's graded first term and answers for the rest
    # of the year, so those are the only records it is asked about.
    stmt = scoped(
        select(StudentPace)
        .where(StudentPace.grading_period == ml_client.FIRST_TERM)
        .order_by(StudentPace.pace_id),
        StudentPace,
        scope,
    )
    if payload.student_id:
        stmt = stmt.where(StudentPace.student_id == payload.student_id)
    if payload.subject:
        stmt = stmt.where(StudentPace.subject == payload.subject)
    rows = [
        row for row in (await db.execute(stmt)).scalars().all()
        if ml_client.first_term_ready(row)
    ]

    enrollments = {
        e.enrollment_id: e
        for e in (
            await db.execute(
                scoped(select(StudentEnrollment), StudentEnrollment, scope)
            )
        ).scalars()
    }
    lookups = await Lookups.load(db, school_id=scope)

    allowed: Optional[set[int]] = None
    if current.role != ROLE_ADMIN:
        allowed = set(await sections_for_teacher(db, current.id, school_id=scope))

    selected = []
    for row in rows:
        enrollment = enrollments.get(row.enrollment_id)
        if payload.section_id and (
            enrollment is None or enrollment.section_id != payload.section_id
        ):
            continue
        if allowed is not None and (
            enrollment is None or enrollment.section_id not in allowed
        ):
            continue
        label = (
            lookups.grade_levels.get(enrollment.grade_level_id)
            if enrollment is not None
            else None
        )
        selected.append((row, label))
        if len(selected) >= payload.limit:
            break

    if not selected:
        return RescoreResponse(requested=0, scored=0, fallback=0, source="none",
                               detail="No graded first-term records matched. The model "
                               "starts once a first term has written works, "
                               "performance tasks and examinations.")

    # The AI service caps a batch, so send it in chunks of that size.
    chunk = max(1, settings.AI_BATCH_SIZE)
    fallbacks, versions, first_error = 0, set(), None

    for start in range(0, len(selected), chunk):
        part = selected[start:start + chunk]
        results = await ml_client.predict_many(part)
        for (row, _label), result in zip(part, results):
            if result.source == "fallback":
                fallbacks += 1
                first_error = first_error or result.error
            else:
                versions.add(result.model_version)
            db.add(
                AIRiskPrediction(
                    school_id=scope,
                    student_id=row.student_id,
                    enrollment_id=row.enrollment_id,
                    pace_id=row.pace_id,
                    subject=row.subject,
                    **result.as_columns(),
                )
            )

    await db.commit()

    if fallbacks == 0:
        source = "model"
    elif fallbacks == len(selected):
        source = "fallback"
    else:
        source = "mixed"

    return RescoreResponse(
        requested=len(selected),
        scored=len(selected),
        fallback=fallbacks,
        source=source,
        model_version=next(iter(versions), None),
        detail=first_error,
    )
