"""PACE encoding and the early-warning list.

Every time a PACE record is written the derived fields (percentage, booklets
behind, risk score/level) are recomputed and the matching early-warning row is
created, updated or cleared — so the warning list can never drift from the
progress data it is based on.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_admin, get_current_user, school_scope
from app.api.scoping import get_scoped, get_scoped_optional, scoped
from app.db.session import get_db
from app.models.academic import GradeLevel, Subject, TeacherAssignment
from app.models.staff import ROLE_ADMIN, Staff
from app.models.students import (
    AIRiskPrediction,
    ClassRecordSetup,
    DataQualityLog,
    EarlyWarning,
    Student,
    StudentEnrollment,
    StudentPace,
)
from app.schemas.students import (
    BulkPaceRequest,
    ClassRecordSetupResponse,
    ClassRecordSetupUpdate,
    DataQualityCreate,
    DataQualityResponse,
    PaceCreate,
    PaceResponse,
    PaceUpdate,
    WarningResponse,
    WarningUpdate,
)
from app.services import grading, ml_client, risk
from app.services.audit import Action, log_event
from app.services.enrichment import (Lookups, latest_predictions,
                                     term_grades_by_subject,
                                     school_passing_mark as enrichment_passing_mark,
                                     sections_for_teacher)

router = APIRouter(tags=["PACE & Early warning"])


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── Class-record arithmetic ───────────────────────────────────────────────────
SCORE_ARRAYS = ("ww_scores", "pt_scores", "exam_scores")

DERIVED_COLUMNS = (
    "ww_total_raw", "ww_percent", "ww_weighted",
    "pt_total_raw", "pt_percent", "pt_weighted",
    "exam_percent", "exam_weighted",
    "initial_grade", "transmuted_grade", "descriptor",
    "pre_exam_weighted", "pre_exam_max",
)


def _default_setup(template: str = grading.CORE) -> dict:
    profile = grading.TEMPLATES.get(template, grading.TEMPLATES[grading.CORE])
    return {
        "template": template,
        "ww_highest": [], "pt_highest": [], "exam_highest": [],
        "ww_weight": profile["ww_weight"],
        "pt_weight": profile["pt_weight"],
        "ex_weight": profile["ex_weight"],
        "exam_subweights": list(grading.EXAM_SUBWEIGHTS),
    }


def _loads(raw, fallback=None):
    """Tolerant JSON read. A row written before a column existed, or one a
    migration left as an empty string, must not take a save down with it."""
    if not raw:
        return [] if fallback is None else fallback
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return [] if fallback is None else fallback
    return value if isinstance(value, list) else ([] if fallback is None else fallback)


async def _load_setup(
    db: AsyncSession,
    school_id: int,
    section_id: Optional[int],
    subject_id: Optional[int],
    grading_period: Optional[int],
) -> dict:
    """The template, highest possible scores and weights for one class record.

    Falls back to the core template's defaults when a teacher has not filled
    the header in yet, so encoding can begin before the sheet is set up.
    Without a highest possible score there is no denominator, so percentages
    stay blank until one is entered — blank, not zero, and never guessed.
    """
    if not (section_id and subject_id and grading_period):
        return _default_setup()
    row = (
        await db.execute(
            scoped(
                select(ClassRecordSetup).where(
                    ClassRecordSetup.section_id == section_id,
                    ClassRecordSetup.subject_id == subject_id,
                    ClassRecordSetup.grading_period == grading_period,
                ),
                ClassRecordSetup,
                school_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return _default_setup()
    return {
        "template": row.template or grading.CORE,
        "ww_highest": _loads(row.ww_highest),
        "pt_highest": _loads(row.pt_highest),
        "exam_highest": _loads(row.exam_highest),
        "ww_weight": row.ww_weight,
        "pt_weight": row.pt_weight,
        "ex_weight": row.ex_weight,
        "exam_subweights": _loads(row.exam_subweights,
                                  list(grading.EXAM_SUBWEIGHTS)),
    }


def _assessment_columns(payload) -> dict:
    """Turn the score arrays a client sent into the text the columns hold."""
    out = {}
    for name in SCORE_ARRAYS:
        value = getattr(payload, name, None)
        if value is not None:
            out[name] = json.dumps(value)
    return out


def _recompute_grades(row: StudentPace, setup: dict) -> None:
    """Rewrite every derived column on a record from its stored scores.

    Called on every write. Nothing derived is ever accepted from a client, so
    the totals, the initial grade and the term grade on screen are always the
    ones the stored scores produce.
    """
    result = grading.compute(
        ww_scores=_loads(row.ww_scores),
        pt_scores=_loads(row.pt_scores),
        exam_scores=_loads(row.exam_scores),
        ww_highest=setup["ww_highest"],
        pt_highest=setup["pt_highest"],
        exam_highest=setup["exam_highest"],
        template=setup["template"],
        ww_weight=setup["ww_weight"],
        pt_weight=setup["pt_weight"],
        ex_weight=setup["ex_weight"],
        exam_subweights=setup["exam_subweights"],
    )
    for column in DERIVED_COLUMNS:
        setattr(row, column, result[column])
    row.exam_parts = json.dumps(result["exam_parts"])
    row.record_complete = bool(result["complete"])


# Below the line is what the system flags; the passing mark is what it warns
# about losing. Both come from the school, and only the second is editable.
RISK_LINE = 80.0


def _apply_evaluation(
    row: StudentPace,
    passing_mark: float = 75.0,
    term_grades: Optional[list] = None,
) -> dict:
    """Recompute the derived columns on a record and score it.

    The scoring lives in app.services.risk, and every screen that shows a
    risk calls the same function — which is what stops the Students page
    and the early-warning list from disagreeing about the same learner.

    ``term_grades`` is every term this learner has in this subject. The
    final grade is their mean, so that is what the learner is judged on
    once any term is finished. Before then there is no year yet and the
    reading falls back to the term in progress.
    """
    evaluation = risk.evaluate_year(
        row, term_grades or [], RISK_LINE, passing_mark
    )
    if evaluation["basis"] == "units":
        row.pace_percent = evaluation["pace_percent"]
        row.paces_behind = evaluation["paces_behind"]
    else:
        # A class-record school counts no booklets; leaving stale values in
        # these columns would show as phantom progress on the roster.
        row.pace_percent = 0.0
        row.paces_behind = 0
    return evaluation


# One definition, in app.services.enrichment, so the save path and the list
# cannot end up reading different marks.
_passing_mark = enrichment_passing_mark


async def _sync_warning(
    db: AsyncSession,
    row: StudentPace,
    evaluation: dict,
    teacher_name: str,
    school_id: int,
) -> None:
    """Keep the early-warning table in step with a PACE record."""
    existing = (
        await db.execute(
            scoped(
                select(EarlyWarning).where(
                    EarlyWarning.student_id == row.student_id,
                    EarlyWarning.subject == row.subject,
                ),
                EarlyWarning,
                school_id,
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
            existing.standing = evaluation["standing"]
            existing.basis = evaluation["basis"]
            existing.risk_score = evaluation["risk_score"]
        return

    previous_percent = existing.pace_percent if existing is not None else None
    trend = risk.trend_from(previous_percent, row.pace_percent)

    if existing is None:
        db.add(
            EarlyWarning(
                school_id=school_id,
                student_id=row.student_id,
                enrollment_id=row.enrollment_id,
                subject=row.subject,
                teacher=teacher_name,
                risk_level=evaluation["risk_level"],
                risk_score=evaluation["risk_score"],
                paces_behind=row.paces_behind,
                pace_percent=row.pace_percent,
                standing=evaluation["standing"],
                basis=evaluation["basis"],
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
        existing.standing = evaluation["standing"]
        existing.basis = evaluation["basis"]
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


async def _grade_label(
    db: AsyncSession, enrollment_id: Optional[int], school_id: int
) -> Optional[str]:
    """The grade-level name for an enrolment — one of the model's nine inputs."""
    if enrollment_id is None:
        return None
    enrollment = await get_scoped_optional(
        db, StudentEnrollment, enrollment_id, school_id
    )
    if enrollment is None:
        return None
    level = await get_scoped_optional(
        db, GradeLevel, enrollment.grade_level_id, school_id
    )
    return level.name if level is not None else None


async def _record_prediction(
    db: AsyncSession,
    row: StudentPace,
    grade_label: Optional[str],
    school_id: int,
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
        school_id=school_id,
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
    scope: int = Depends(school_scope),
    db: AsyncSession = Depends(get_db),
):
    passing_mark = await _passing_mark(db, scope)
    stmt = scoped(
        select(StudentPace).order_by(StudentPace.student_id, StudentPace.subject),
        StudentPace,
        scope,
    )
    if student_id:
        stmt = stmt.where(StudentPace.student_id == student_id)
    if enrollment_id:
        stmt = stmt.where(StudentPace.enrollment_id == enrollment_id)
    if subject:
        stmt = stmt.where(StudentPace.subject == subject)
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

    predictions = await latest_predictions(
        db, [row.pace_id for row in rows], school_id=scope
    )

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
        # The same scorer the save path and the Students page use, so the
        # badge on the roster matches the one on the early-warning list.
        item.risk_level = risk.evaluate_row(row, passing_mark)["risk_level"]
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
    scope: int = Depends(school_scope),
    db: AsyncSession = Depends(get_db),
):
    await get_scoped(db, Student, payload.student_id, scope, "Student")
    await get_scoped(db, StudentEnrollment, payload.enrollment_id, scope, "Enrolment")
    if payload.current_pace > payload.total_paces:
        raise HTTPException(
            status_code=400,
            detail="The current PACE number cannot exceed the total for the subject.",
        )

    existing = (
        await db.execute(
            scoped(
                select(StudentPace).where(
                    StudentPace.enrollment_id == payload.enrollment_id,
                    StudentPace.subject == payload.subject,
                ),
                StudentPace,
                scope,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=f"A PACE record for {payload.subject} already exists — edit it instead.",
        )

    fields = payload.model_dump(exclude=set(SCORE_ARRAYS))
    row = StudentPace(**fields, **_assessment_columns(payload),
                      recorded_by=current.id, school_id=scope)
    # No section or period is supplied on this path, so the record is graded
    # against the DepEd default split until the class record's own header is
    # saved, at which point every row in it is recomputed.
    _recompute_grades(row, _default_setup())
    evaluation = _apply_evaluation(row, await _passing_mark(db, scope))
    db.add(row)
    await db.flush()  # assigns pace_id, which the prediction row references
    await _sync_warning(db, row, evaluation, current.full_name, scope)
    prediction = await _record_prediction(
        db, row, await _grade_label(db, row.enrollment_id, scope), scope
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
    scope: int = Depends(school_scope),
    db: AsyncSession = Depends(get_db),
):
    row = await get_scoped(db, StudentPace, pace_id, scope, "PACE record")
    sent = payload.model_dump(exclude_unset=True)
    for field, value in sent.items():
        if field in SCORE_ARRAYS:
            setattr(row, field, json.dumps(value) if value is not None else None)
        else:
            setattr(row, field, value)
    if any(field in sent for field in SCORE_ARRAYS):
        _recompute_grades(row, _default_setup())
    if row.current_pace > row.total_paces:
        raise HTTPException(
            status_code=400,
            detail="The current PACE number cannot exceed the total for the subject.",
        )
    row.recorded_by = current.id
    row.updated_at = _now()
    evaluation = _apply_evaluation(row, await _passing_mark(db, scope))
    await _sync_warning(db, row, evaluation, current.full_name, scope)
    prediction = await _record_prediction(
        db, row, await _grade_label(db, row.enrollment_id, scope), scope
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
    scope: int = Depends(school_scope),
    db: AsyncSession = Depends(get_db),
):
    """Encode a whole section in one submit — the teacher's main workflow."""
    saved, skipped = 0, []
    touched: list[StudentPace] = []

    # The sheet's header: highest possible score per item and the three
    # weights. Loaded once for the whole save, and loaded here rather than
    # taken from the request, so a client cannot choose the numbers a grade
    # is computed from.
    setup = await _load_setup(
        db, scope, payload.section_id, payload.subject_id, payload.grading_period
    )
    passing_mark = await _passing_mark(db, scope)
    # Which term is being encoded. Everything written below belongs to it,
    # and the same subject's records for other terms are left untouched.
    term = payload.grading_period or 1
    # Every term already recorded for these learners, so each row can be
    # judged on its year. Loaded once rather than per learner.
    year_terms = await term_grades_by_subject(
        db, school_id=scope,
        student_ids=[e.student_id for e in payload.entries],
    )

    for entry in payload.entries:
        if entry.current_pace > entry.total_paces:
            skipped.append(
                f"{entry.subject} for student {entry.student_id}: PACE number above the total"
            )
            continue

        if await get_scoped_optional(db, Student, entry.student_id, scope) is None:
            skipped.append(
                f"{entry.subject} for student {entry.student_id}: not found in this school"
            )
            continue
        if (
            await get_scoped_optional(
                db, StudentEnrollment, entry.enrollment_id, scope
            )
            is None
        ):
            skipped.append(
                f"{entry.subject} for student {entry.student_id}: "
                "enrolment not found in this school"
            )
            continue

        row = (
            await db.execute(
                scoped(
                    select(StudentPace).where(
                        StudentPace.enrollment_id == entry.enrollment_id,
                        StudentPace.subject == entry.subject,
                        StudentPace.grading_period == term,
                    ),
                    StudentPace,
                    scope,
                )
            )
        ).scalar_one_or_none()

        if row is None:
            row = StudentPace(
                school_id=scope,
                student_id=entry.student_id,
                enrollment_id=entry.enrollment_id,
                subject=entry.subject,
                grading_period=term,
            )
            db.add(row)

        row.current_pace = entry.current_pace
        row.expected_pace = entry.expected_pace
        row.total_paces = entry.total_paces
        row.attendance = entry.attendance
        row.average_score = entry.average_score
        row.recorded_by = current.id
        row.updated_at = _now()

        # Score arrays are only written when the teacher sent them, so a save
        # that touches only unit progress cannot wipe a class record already
        # encoded. Within an array, however, a null IS meaningful: it is a
        # cell the teacher cleared, and clearing a cell has to stick.
        for field_name in SCORE_ARRAYS:
            value = getattr(entry, field_name, None)
            if value is not None:
                setattr(row, field_name, json.dumps(value))
        for field_name in ("absences", "extracurricular_count"):
            value = getattr(entry, field_name, None)
            if value is not None:
                setattr(row, field_name, value)

        _recompute_grades(row, setup)
        # The row being saved is the newest word on its own term, so it
        # replaces whatever the preloaded view held for that term.
        grades = list(year_terms.get((entry.enrollment_id, entry.subject), []))
        while len(grades) < term:
            grades.append(None)
        grades[term - 1] = row.transmuted_grade
        evaluation = _apply_evaluation(row, passing_mark, grades)
        await db.flush()
        await _sync_warning(db, row, evaluation, current.full_name, scope)
        touched.append(row)
        saved += 1

    # One batch call for the whole section instead of one request per student:
    # on a free Render instance that is the difference between a save that
    # feels instant and one that makes forty round trips.
    ai_summary = await _score_batch(db, touched, scope)

    await log_event(
        db, Action.PACE_RECORDED, actor_id=current.id, detail=f"bulk saved={saved}",
        request=request,
    )
    await db.commit()
    return {"saved": saved, "skipped": skipped, "ai": ai_summary}


async def _score_batch(
    db: AsyncSession, rows: list[StudentPace], school_id: int
) -> dict:
    """Score a list of saved records in one call and log every answer.

    First-term records are deliberately left unscored. The model is asked
    whether a learner will finish the SUBJECT below the line, and during
    the first term there is no finished term to reason from — the only
    evidence is the term in progress, which is what the rule reads. Asking
    the model anyway would produce a number with a confidence it has not
    earned, and a teacher would read it as a forecast.
    """
    if not rows:
        return {"scored": 0, "fallback": 0, "source": "none"}

    scorable = [r for r in rows if (r.grading_period or 1) > 1]
    held_back = len(rows) - len(scorable)
    if not scorable:
        return {
            "scored": 0,
            "fallback": 0,
            "source": "rule",
            "held_back": held_back,
            "note": (
                "First term — the risk shown is the rule's. The model starts "
                "once a term is finished and there is a year to reason about."
            ),
        }
    rows = scorable

    labels: dict[int, Optional[str]] = {}
    for row in rows:
        if row.enrollment_id not in labels:
            labels[row.enrollment_id] = await _grade_label(
                db, row.enrollment_id, school_id
            )

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
                school_id=school_id,
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
        # First-term records that were deliberately not sent to the model.
        "held_back": held_back,
        "model_version": next(iter(versions), None),
        "detail": results[0].error if fallbacks else None,
    }


@router.get("/api/class-record/setup", response_model=ClassRecordSetupResponse)
async def get_class_record_setup(
    section_id: int = Query(...),
    subject_id: int = Query(...),
    grading_period: int = Query(1, ge=1, le=4),
    current: Staff = Depends(get_current_user),
    scope: int = Depends(school_scope),
    db: AsyncSession = Depends(get_db),
):
    """The HIGHEST POSSIBLE SCORE row and the weights for one class record.

    Returns the DepEd default split when the header has not been filled in,
    so a teacher opening a fresh record sees a usable sheet rather than an
    error.
    """
    setup = await _load_setup(db, scope, section_id, subject_id, grading_period)
    return ClassRecordSetupResponse(
        section_id=section_id, subject_id=subject_id,
        grading_period=grading_period, **setup,
    )


@router.put("/api/class-record/setup", response_model=ClassRecordSetupResponse)
async def put_class_record_setup(
    payload: ClassRecordSetupUpdate,
    request: Request,
    section_id: int = Query(...),
    subject_id: int = Query(...),
    grading_period: int = Query(1, ge=1, le=4),
    current: Staff = Depends(get_current_user),
    scope: int = Depends(school_scope),
    db: AsyncSession = Depends(get_db),
):
    """Save the header, then rebuild every grade in the record.

    Changing a highest possible score or a weight changes every learner's
    percentage and grade. Recomputing them here is what stops the sheet from
    showing grades derived from a denominator that no longer exists.
    """
    await get_scoped(db, Subject, subject_id, scope)

    row = (
        await db.execute(
            scoped(
                select(ClassRecordSetup).where(
                    ClassRecordSetup.section_id == section_id,
                    ClassRecordSetup.subject_id == subject_id,
                    ClassRecordSetup.grading_period == grading_period,
                ),
                ClassRecordSetup,
                scope,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = ClassRecordSetup(
            school_id=scope, section_id=section_id, subject_id=subject_id,
            grading_period=grading_period,
        )
        db.add(row)

    row.template = payload.template
    row.ww_highest = json.dumps(payload.ww_highest)
    row.pt_highest = json.dumps(payload.pt_highest)
    row.exam_highest = json.dumps(payload.exam_highest)
    row.ww_weight = payload.ww_weight
    row.pt_weight = payload.pt_weight
    row.ex_weight = payload.ex_weight
    row.exam_subweights = json.dumps(payload.exam_subweights)
    row.updated_by = current.id
    row.updated_at = _now()
    await db.flush()

    setup = await _load_setup(db, scope, section_id, subject_id, grading_period)
    subject = await get_scoped(db, Subject, subject_id, scope)
    affected = (
        await db.execute(
            scoped(
                select(StudentPace)
                .join(StudentEnrollment,
                      StudentEnrollment.enrollment_id == StudentPace.enrollment_id)
                .where(StudentEnrollment.section_id == section_id,
                       StudentPace.subject == subject.subject_name),
                StudentPace,
                scope,
            )
        )
    ).scalars().all()
    for pace in affected:
        _recompute_grades(pace, setup)

    await log_event(
        db, Action.PACE_RECORDED, actor_id=current.id,
        detail=(f"class record header saved for section {section_id} "
                f"subject {subject_id} period {grading_period}; "
                f"{len(affected)} records regraded"),
        request=request,
    )
    await db.commit()
    return ClassRecordSetupResponse(
        section_id=section_id, subject_id=subject_id,
        grading_period=grading_period, **setup,
    )


@router.delete("/api/paces/{pace_id}", status_code=204)
async def delete_pace(
    pace_id: int,
    _: Staff = Depends(get_current_admin),
    scope: int = Depends(school_scope),
    db: AsyncSession = Depends(get_db),
):
    row = await get_scoped(db, StudentPace, pace_id, scope, "PACE record")
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
    scope: int = Depends(school_scope),
    db: AsyncSession = Depends(get_db),
):
    stmt = scoped(
        select(EarlyWarning).order_by(EarlyWarning.risk_score.desc()),
        EarlyWarning,
        scope,
    )
    if not include_resolved:
        stmt = stmt.where(EarlyWarning.is_resolved.is_(False))
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
    _: Staff = Depends(get_current_user),
    scope: int = Depends(school_scope),
    db: AsyncSession = Depends(get_db),
):
    rows = (
        await db.execute(
            scoped(
                select(EarlyWarning).where(EarlyWarning.is_resolved.is_(False)),
                EarlyWarning,
                scope,
            )
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
    scope: int = Depends(school_scope),
    db: AsyncSession = Depends(get_db),
):
    row = await get_scoped(db, EarlyWarning, warning_id, scope, "Warning")
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
    scope: int = Depends(school_scope),
    db: AsyncSession = Depends(get_db),
):
    stmt = scoped(
        select(DataQualityLog).order_by(DataQualityLog.created_at.desc()),
        DataQualityLog,
        scope,
    )
    if resolved is not None:
        stmt = stmt.where(DataQualityLog.resolved.is_(resolved))
    rows = (await db.execute(stmt)).scalars().all()
    students = {
        s.student_id: s.full_name
        for s in (
            await db.execute(scoped(select(Student), Student, scope))
        ).scalars()
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
    scope: int = Depends(school_scope),
    db: AsyncSession = Depends(get_db),
):
    if await get_scoped_optional(db, Student, payload.student_id, scope) is None:
        raise HTTPException(status_code=400, detail="That student does not exist.")
    # Both of these default to something already in scope when left out — the
    # caller for the teacher, nothing at all for the PACE record — so they are
    # only worth checking when the request actually names one.
    if payload.teacher_id is not None and (
        await get_scoped_optional(db, Staff, payload.teacher_id, scope) is None
    ):
        raise HTTPException(status_code=400, detail="That teacher does not exist.")
    if payload.student_pace_id is not None and (
        await get_scoped_optional(db, StudentPace, payload.student_pace_id, scope)
        is None
    ):
        raise HTTPException(status_code=400, detail="That PACE record does not exist.")

    row = DataQualityLog(**payload.model_dump(), school_id=scope)
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
    scope: int = Depends(school_scope),
    db: AsyncSession = Depends(get_db),
):
    row = await get_scoped(db, DataQualityLog, log_id, scope, "Log entry")
    row.resolved = True
    row.resolved_date = _now()
    await db.commit()
    await db.refresh(row)
    return DataQualityResponse.model_validate(row)
