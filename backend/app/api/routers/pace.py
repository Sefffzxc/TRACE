"""PACE encoding and the early-warning list.

Every time a PACE record is written the derived fields (percentage, booklets
behind, risk score/level) are recomputed and the matching early-warning row is
created, updated or cleared — so the warning list can never drift from the
progress data it is based on.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_admin, get_current_user
from app.db.session import get_db
from app.models.academic import GradeLevel, Subject, TeacherAssignment
from app.models.staff import ROLE_ADMIN, Staff
from app.models.students import (
    AIRiskPrediction,
    DataQualityLog,
    EarlyWarning,
    Student,
    StudentEnrollment,
    StudentPace,
)
from app.schemas.students import (
    BulkPaceRequest,
    DataQualityCreate,
    DataQualityResponse,
    PaceCreate,
    PaceResponse,
    PaceUpdate,
    WarningResponse,
    WarningUpdate,
)
from app.services import ml_client, risk
from app.services.audit import Action, log_event
from app.services.enrichment import Lookups, latest_predictions, sections_for_teacher

router = APIRouter(tags=["PACE & Early warning"])


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _apply_evaluation(row: StudentPace) -> dict:
    """Recompute the derived columns on a PACE row."""
    evaluation = risk.evaluate(
        row.current_pace, row.expected_pace, row.total_paces, row.attendance
    )
    row.pace_percent = evaluation["pace_percent"]
    row.paces_behind = evaluation["paces_behind"]
    return evaluation


async def _sync_warning(
    db: AsyncSession, row: StudentPace, evaluation: dict, teacher_name: str
) -> None:
    """Keep the early-warning table in step with a PACE record."""
    existing = (
        await db.execute(
            select(EarlyWarning).where(
                EarlyWarning.student_id == row.student_id,
                EarlyWarning.subject == row.subject,
            )
        )
    ).scalar_one_or_none()

    if evaluation["risk_level"] == "low":
        # No longer a concern — close any open flag rather than deleting history.
        if existing is not None and not existing.is_resolved:
            existing.is_resolved = True
            existing.resolved_at = _now()
            existing.risk_level = "low"
            existing.status = "On Track"
            existing.pace_percent = row.pace_percent
            existing.paces_behind = row.paces_behind
            existing.attendance = row.attendance
            existing.risk_score = evaluation["risk_score"]
        return

    previous_percent = existing.pace_percent if existing is not None else None
    trend = risk.trend_from(previous_percent, row.pace_percent)

    if existing is None:
        db.add(
            EarlyWarning(
                student_id=row.student_id,
                enrollment_id=row.enrollment_id,
                subject=row.subject,
                teacher=teacher_name,
                risk_level=evaluation["risk_level"],
                risk_score=evaluation["risk_score"],
                paces_behind=row.paces_behind,
                pace_percent=row.pace_percent,
                attendance=row.attendance,
                status=evaluation["status"],
                trend=trend,
                last_activity="Today",
                intervention=evaluation["intervention"],
            )
        )
    else:
        existing.enrollment_id = row.enrollment_id
        existing.teacher = teacher_name
        existing.risk_level = evaluation["risk_level"]
        existing.risk_score = evaluation["risk_score"]
        existing.paces_behind = row.paces_behind
        existing.pace_percent = row.pace_percent
        existing.attendance = row.attendance
        existing.status = evaluation["status"]
        existing.trend = trend
        existing.last_activity = "Today"
        existing.intervention = evaluation["intervention"]
        existing.is_resolved = False
        existing.resolved_at = None
        existing.updated_at = _now()

def _attach_ai(item, prediction) -> None:
    """Copy a logged prediction onto a PACE response.

    The threshold and model version go with it so the UI can label the number
    correctly instead of assuming either one.
    """
    item.ai_risk_score = prediction.risk_score
    item.ai_risk_level = prediction.risk_level
    item.ai_source = prediction.source
    item.ai_used_threshold = prediction.used_threshold
    item.ai_model_version = prediction.model_version
    item.ai_used_baseline_defaults = prediction.used_baseline_defaults
    item.ai_primary_factor = prediction.primary_factor


async def _grade_label(db: AsyncSession, enrollment_id: Optional[int]) -> Optional[str]:
    """The grade-level name for an enrolment — one of the model's nine inputs."""
    if enrollment_id is None:
        return None
    enrollment = await db.get(StudentEnrollment, enrollment_id)
    if enrollment is None:
        return None
    level = await db.get(GradeLevel, enrollment.grade_level_id)
    return level.name if level is not None else None


async def _record_prediction(
    db: AsyncSession, row: StudentPace, grade_label: Optional[str]
) -> AIRiskPrediction:
    """Ask the classifier about this record and log the answer.

    Appends — never updates. The history of what the model said, and on what
    input, is part of the evidence trail for the study, so a re-score adds a
    row rather than overwriting one.

    This cannot fail the save: ``ml_client.predict`` catches everything and
    returns a rule-based result marked ``source="fallback"`` instead.
    """
    result = await ml_client.predict(row, grade_label)
    prediction = AIRiskPrediction(
        student_id=row.student_id,
        enrollment_id=row.enrollment_id,
        pace_id=row.pace_id,
        subject=row.subject,
        **result.as_columns(),
    )
    db.add(prediction)
    return prediction


# ══════════════════════════════════════════════════════════════════════════════
# PACE records
# ══════════════════════════════════════════════════════════════════════════════
@router.get("/api/paces", response_model=List[PaceResponse])
async def list_paces(
    student_id: Optional[int] = None,
    enrollment_id: Optional[int] = None,
    section_id: Optional[int] = None,
    subject: Optional[str] = None,
    mine: bool = Query(default=False),
    current: Staff = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(StudentPace).order_by(StudentPace.student_id, StudentPace.subject)
    if student_id:
        stmt = stmt.where(StudentPace.student_id == student_id)
    if enrollment_id:
        stmt = stmt.where(StudentPace.enrollment_id == enrollment_id)
    if subject:
        stmt = stmt.where(StudentPace.subject == subject)
    rows = (await db.execute(stmt)).scalars().all()

    lookups = await Lookups.load(db)
    students = {s.student_id: s for s in (await db.execute(select(Student))).scalars()}
    enrollments = {
        e.enrollment_id: e
        for e in (await db.execute(select(StudentEnrollment))).scalars()
    }

    allowed: Optional[set[int]] = None
    if mine and current.role != ROLE_ADMIN:
        allowed = set(await sections_for_teacher(db, current.id))

    predictions = await latest_predictions(db, [row.pace_id for row in rows])

    out = []
    for row in rows:
        enrollment = enrollments.get(row.enrollment_id)
        if section_id and (enrollment is None or enrollment.section_id != section_id):
            continue
        if allowed is not None and (
            enrollment is None or enrollment.section_id not in allowed
        ):
            continue

        item = PaceResponse.model_validate(row)
        student = students.get(row.student_id)
        item.student_name = student.full_name if student else None
        if enrollment is not None:
            item.section_name = lookups.sections.get(enrollment.section_id)
            item.grade_level_name = lookups.grade_levels.get(enrollment.grade_level_id)
        item.risk_level = risk.risk_level(
            risk.risk_score(row.paces_behind, row.pace_percent, row.attendance, row.total_paces)
        )
        prediction = predictions.get(row.pace_id)
        if prediction is not None:
            _attach_ai(item, prediction)
        out.append(item)
    return out


@router.post("/api/paces", response_model=PaceResponse, status_code=201)
async def create_pace(
    payload: PaceCreate,
    request: Request,
    current: Staff = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if await db.get(Student, payload.student_id) is None:
        raise HTTPException(status_code=404, detail="Student not found.")
    enrollment = await db.get(StudentEnrollment, payload.enrollment_id)
    if enrollment is None:
        raise HTTPException(status_code=404, detail="Enrolment not found.")
    if payload.current_pace > payload.total_paces:
        raise HTTPException(
            status_code=400,
            detail="The current PACE number cannot exceed the total for the subject.",
        )

    existing = (
        await db.execute(
            select(StudentPace).where(
                StudentPace.enrollment_id == payload.enrollment_id,
                StudentPace.subject == payload.subject,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=f"A PACE record for {payload.subject} already exists — edit it instead.",
        )

    row = StudentPace(**payload.model_dump(), recorded_by=current.id)
    evaluation = _apply_evaluation(row)
    db.add(row)
    await db.flush()  # assigns pace_id, which the prediction row references
    await _sync_warning(db, row, evaluation, current.full_name)
    prediction = await _record_prediction(
        db, row, await _grade_label(db, row.enrollment_id)
    )
    await log_event(
        db, Action.PACE_RECORDED, actor_id=current.id,
        detail=f"student={payload.student_id} subject={payload.subject}", request=request,
    )
    await db.commit()
    await db.refresh(row)

    item = PaceResponse.model_validate(row)
    item.risk_level = evaluation["risk_level"]
    _attach_ai(item, prediction)
    return item


@router.patch("/api/paces/{pace_id}", response_model=PaceResponse)
async def update_pace(
    pace_id: int,
    payload: PaceUpdate,
    current: Staff = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    row = await db.get(StudentPace, pace_id)
    if row is None:
        raise HTTPException(status_code=404, detail="PACE record not found.")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(row, field, value)
    if row.current_pace > row.total_paces:
        raise HTTPException(
            status_code=400,
            detail="The current PACE number cannot exceed the total for the subject.",
        )
    row.recorded_by = current.id
    row.updated_at = _now()
    evaluation = _apply_evaluation(row)
    await _sync_warning(db, row, evaluation, current.full_name)
    prediction = await _record_prediction(
        db, row, await _grade_label(db, row.enrollment_id)
    )
    await db.commit()
    await db.refresh(row)

    item = PaceResponse.model_validate(row)
    item.risk_level = evaluation["risk_level"]
    _attach_ai(item, prediction)
    return item


@router.post("/api/paces/bulk")
async def bulk_upsert_paces(
    payload: BulkPaceRequest,
    request: Request,
    current: Staff = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Encode a whole section in one submit — the teacher's main workflow."""
    saved, skipped = 0, []
    touched: list[StudentPace] = []

    for entry in payload.entries:
        if entry.current_pace > entry.total_paces:
            skipped.append(
                f"{entry.subject} for student {entry.student_id}: PACE number above the total"
            )
            continue

        row = (
            await db.execute(
                select(StudentPace).where(
                    StudentPace.enrollment_id == entry.enrollment_id,
                    StudentPace.subject == entry.subject,
                )
            )
        ).scalar_one_or_none()

        if row is None:
            row = StudentPace(
                student_id=entry.student_id,
                enrollment_id=entry.enrollment_id,
                subject=entry.subject,
            )
            db.add(row)

        row.current_pace = entry.current_pace
        row.expected_pace = entry.expected_pace
        row.total_paces = entry.total_paces
        row.attendance = entry.attendance
        row.average_score = entry.average_score
        row.recorded_by = current.id
        row.updated_at = _now()

        # Assessment values are only written when the teacher supplied them —
        # a blank cell in the bulk form must not wipe a figure already encoded.
        for field_name in ml_client.MODEL_FIELDS:
            if field_name == "grade_level":
                continue  # read from the enrolment, not the form
            value = getattr(entry, field_name, None)
            if value is not None:
                setattr(row, field_name, value)

        evaluation = _apply_evaluation(row)
        await db.flush()
        await _sync_warning(db, row, evaluation, current.full_name)
        touched.append(row)
        saved += 1

    # One batch call for the whole section instead of one request per student:
    # on a free Render instance that is the difference between a save that
    # feels instant and one that makes forty round trips.
    ai_summary = await _score_batch(db, touched)

    await log_event(
        db, Action.PACE_RECORDED, actor_id=current.id, detail=f"bulk saved={saved}",
        request=request,
    )
    await db.commit()
    return {"saved": saved, "skipped": skipped, "ai": ai_summary}


async def _score_batch(db: AsyncSession, rows: list[StudentPace]) -> dict:
    """Score a list of saved PACE rows in one call and log every answer."""
    if not rows:
        return {"scored": 0, "fallback": 0, "source": "none"}

    labels: dict[int, Optional[str]] = {}
    for row in rows:
        if row.enrollment_id not in labels:
            labels[row.enrollment_id] = await _grade_label(db, row.enrollment_id)

    results = await ml_client.predict_many(
        [(row, labels.get(row.enrollment_id)) for row in rows]
    )

    fallbacks = 0
    versions: set[str] = set()
    for row, result in zip(rows, results):
        if result.source == "fallback":
            fallbacks += 1
        else:
            versions.add(result.model_version)
        db.add(
            AIRiskPrediction(
                student_id=row.student_id,
                enrollment_id=row.enrollment_id,
                pace_id=row.pace_id,
                subject=row.subject,
                **result.as_columns(),
            )
        )

    if fallbacks == 0:
        source = "model"
    elif fallbacks == len(rows):
        source = "fallback"
    else:
        source = "mixed"

    return {
        "scored": len(rows),
        "fallback": fallbacks,
        "source": source,
        "model_version": next(iter(versions), None),
        "detail": results[0].error if fallbacks else None,
    }


@router.delete("/api/paces/{pace_id}", status_code=204)
async def delete_pace(
    pace_id: int,
    _: Staff = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    row = await db.get(StudentPace, pace_id)
    if row is None:
        raise HTTPException(status_code=404, detail="PACE record not found.")
    await db.delete(row)
    await db.commit()


# ══════════════════════════════════════════════════════════════════════════════
# Early warnings
# ══════════════════════════════════════════════════════════════════════════════
@router.get("/api/early-warnings", response_model=List[WarningResponse])
async def list_warnings(
    risk_level: Optional[str] = Query(default=None, description="Comma-separated levels"),
    section_id: Optional[int] = None,
    include_resolved: bool = False,
    mine: bool = Query(default=False),
    current: Staff = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(EarlyWarning).order_by(EarlyWarning.risk_score.desc())
    if not include_resolved:
        stmt = stmt.where(EarlyWarning.is_resolved.is_(False))
    rows = (await db.execute(stmt)).scalars().all()

    lookups = await Lookups.load(db)
    students = {s.student_id: s for s in (await db.execute(select(Student))).scalars()}
    enrollments = {
        e.enrollment_id: e
        for e in (await db.execute(select(StudentEnrollment))).scalars()
    }

    allowed: Optional[set[int]] = None
    if mine and current.role != ROLE_ADMIN:
        allowed = set(await sections_for_teacher(db, current.id))

    wanted = (
        {lvl.strip().lower() for lvl in risk_level.split(",")} if risk_level else None
    )

    out = []
    for row in rows:
        if wanted and row.risk_level not in wanted:
            continue
        enrollment = enrollments.get(row.enrollment_id)
        if section_id and (enrollment is None or enrollment.section_id != section_id):
            continue
        if allowed is not None and (
            enrollment is None or enrollment.section_id not in allowed
        ):
            continue

        item = WarningResponse.model_validate(row)
        student = students.get(row.student_id)
        item.student_name = student.full_name if student else None
        if enrollment is not None:
            item.section_name = lookups.sections.get(enrollment.section_id)
            item.grade_level_name = lookups.grade_levels.get(enrollment.grade_level_id)
        out.append(item)
    return out


@router.get("/api/early-warnings/summary")
async def warning_summary(
    _: Staff = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    rows = (
        await db.execute(
            select(EarlyWarning).where(EarlyWarning.is_resolved.is_(False))
        )
    ).scalars().all()

    counts = {"critical": 0, "high": 0, "moderate": 0, "low": 0}
    for row in rows:
        counts[row.risk_level] = counts.get(row.risk_level, 0) + 1

    by_subject: dict[str, int] = {}
    for row in rows:
        by_subject[row.subject] = by_subject.get(row.subject, 0) + 1

    return {
        "counts": counts,
        "total_flagged": len(rows),
        "students_flagged": len({row.student_id for row in rows}),
        "by_subject": [
            {"subject": name, "count": count}
            for name, count in sorted(by_subject.items(), key=lambda kv: -kv[1])
        ],
    }


@router.patch("/api/early-warnings/{warning_id}", response_model=WarningResponse)
async def update_warning(
    warning_id: int,
    payload: WarningUpdate,
    _: Staff = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    row = await db.get(EarlyWarning, warning_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Warning not found.")
    data = payload.model_dump(exclude_unset=True)
    if data.get("is_resolved"):
        row.resolved_at = _now()
    for field, value in data.items():
        setattr(row, field, value)
    row.updated_at = _now()
    await db.commit()
    await db.refresh(row)
    return WarningResponse.model_validate(row)


# ══════════════════════════════════════════════════════════════════════════════
# Data quality
# ══════════════════════════════════════════════════════════════════════════════
@router.get("/api/data-quality-logs", response_model=List[DataQualityResponse])
async def list_quality_logs(
    resolved: Optional[bool] = None,
    _: Staff = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(DataQualityLog).order_by(DataQualityLog.created_at.desc())
    if resolved is not None:
        stmt = stmt.where(DataQualityLog.resolved.is_(resolved))
    rows = (await db.execute(stmt)).scalars().all()
    students = {
        s.student_id: s.full_name for s in (await db.execute(select(Student))).scalars()
    }
    out = []
    for row in rows:
        item = DataQualityResponse.model_validate(row)
        item.student_name = students.get(row.student_id)
        out.append(item)
    return out


@router.post("/api/data-quality-logs", response_model=DataQualityResponse, status_code=201)
async def create_quality_log(
    payload: DataQualityCreate,
    current: Staff = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    row = DataQualityLog(**payload.model_dump())
    if row.teacher_id is None:
        row.teacher_id = current.id
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return DataQualityResponse.model_validate(row)


@router.patch("/api/data-quality-logs/{log_id}", response_model=DataQualityResponse)
async def resolve_quality_log(
    log_id: int,
    _: Staff = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    row = await db.get(DataQualityLog, log_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Log entry not found.")
    row.resolved = True
    row.resolved_date = _now()
    await db.commit()
    await db.refresh(row)
    return DataQualityResponse.model_validate(row)
