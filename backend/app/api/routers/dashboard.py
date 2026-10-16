"""Everything the dashboard draws, computed server-side in one request."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_admin, get_current_user, school_scope
from app.db.session import get_db
from app.models.academic import GradeLevel, SchoolYear, Section, Subject, TeacherAssignment
from app.models.staff import (
    ROLE_ADMIN,
    ROLE_TEACHER,
    STATUS_PENDING,
    AuditLog,
    Staff,
)
from app.models.schools import School
from app.models.students import EarlyWarning, Student, StudentEnrollment, StudentPace
from app.schemas.students import DashboardStats
from app.services import risk
from app.services.enrichment import Lookups, sections_for_teacher

router = APIRouter(tags=["Dashboard"])

ACTIVITY_LABELS = {
    "register": "New registration",
    "login_success": "Signed in",
    "approve_user": "Account approved",
    "reject_user": "Registration rejected",
    "deactivate_user": "Account deactivated",
    "reactivate_user": "Account reactivated",
    "force_reset_password": "Password reset by admin",
    "password_changed": "Password changed",
    "student_created": "Student added",
    "student_updated": "Student record updated",
    "student_deleted": "Student removed",
    "pace_recorded": "PACE progress encoded",
    "account_locked": "Account locked",
    "account_perm_lock": "Account permanently locked",
}


def _standing_summary(values: list[float], passing_mark: float) -> dict:
    return {
        "average": round(sum(values) / len(values), 1),
        "below": sum(1 for v in values if v < passing_mark),
        "students": len(values),
    }


def _relative(when: Optional[datetime]) -> str:
    if when is None:
        return ""
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    seconds = (datetime.now(timezone.utc) - when).total_seconds()
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)}m ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h ago"
    return f"{int(seconds // 86400)}d ago"


@router.get("/api/dashboard", response_model=DashboardStats)
async def dashboard(
    current: Staff = Depends(get_current_user),
    scope: int = Depends(school_scope),
    db: AsyncSession = Depends(get_db),
):
    # The bands follow the school's own passing mark, and whether the school
    # tracks units decides which half of the figures below the page draws.
    school = await db.get(School, scope)
    passing_mark = float(school.passing_mark) if school and school.passing_mark else 75.0
    lookups = await Lookups.load(db, school_id=scope)
    is_admin = current.role == ROLE_ADMIN

    # A teacher only sees the sections they handle.
    allowed_sections: Optional[set[int]] = None
    if not is_admin:
        allowed_sections = set(
            await sections_for_teacher(db, current.id, school_id=scope)
        )

    enrollments = (
        await db.execute(
            select(StudentEnrollment).where(
                StudentEnrollment.school_id == scope,
                StudentEnrollment.is_active.is_(True),
            )
        )
    ).scalars().all()
    if allowed_sections is not None:
        enrollments = [e for e in enrollments if e.section_id in allowed_sections]

    enrollment_ids = {e.enrollment_id for e in enrollments}
    student_ids = {e.student_id for e in enrollments}
    section_of = {e.enrollment_id: e.section_id for e in enrollments}

    paces = (
        await db.execute(select(StudentPace).where(StudentPace.school_id == scope))
    ).scalars().all()
    paces = [p for p in paces if p.enrollment_id in enrollment_ids]

    warnings = (
        await db.execute(
            select(EarlyWarning).where(
                EarlyWarning.school_id == scope,
                EarlyWarning.is_resolved.is_(False),
            )
        )
    ).scalars().all()
    if allowed_sections is not None:
        warnings = [w for w in warnings if w.enrollment_id in enrollment_ids]

    stats = DashboardStats()
    stats.passing_mark = passing_mark
    if school is not None:
        stats.tracks_units = bool(school.tracks_units)
        stats.unit_label_plural = school.unit_label_plural or "Modules"
        stats.grading_period_label = school.grading_period_label or "Quarter"
    stats.total_students = len(student_ids)
    stats.active_enrollments = len(enrollments)
    stats.total_sections = (
        len(allowed_sections)
        if allowed_sections is not None
        else (
            await db.execute(
                select(func.count())
                .select_from(Section)
                .where(Section.school_id == scope)
            )
        ).scalar_one()
    )
    stats.total_subjects = (
        await db.execute(
            select(func.count())
            .select_from(Subject)
            .where(Subject.school_id == scope, Subject.is_active.is_(True))
        )
    ).scalar_one()

    if is_admin:
        stats.total_teachers = (
            await db.execute(
                select(func.count())
                .select_from(Staff)
                .where(
                    Staff.school_id == scope,
                    Staff.role == ROLE_TEACHER,
                    Staff.is_approved.is_(True),
                )
            )
        ).scalar_one()
        stats.pending_approvals = (
            await db.execute(
                select(func.count())
                .select_from(Staff)
                .where(
                    Staff.school_id == scope,
                    Staff.account_status == STATUS_PENDING,
                )
            )
        ).scalar_one()

    evaluated = [(pace, risk.evaluate_row(pace, passing_mark)) for pace in paces]
    graded = [(pace, ev["standing"]) for pace, ev in evaluated if ev["standing"] is not None]

    if paces:
        stats.average_pace = round(sum(p.pace_percent for p in paces) / len(paces), 1)
    if graded:
        stats.average_standing = round(sum(v for _, v in graded) / len(graded), 1)

    # ── Risk distribution, one entry per student (worst subject wins) ─────────
    per_student: dict[int, float] = {}
    for pace, ev in evaluated:
        per_student[pace.student_id] = max(
            per_student.get(pace.student_id, 0.0), ev["risk_score"]
        )

    buckets = {"critical": 0, "high": 0, "moderate": 0, "low": 0}
    for score in per_student.values():
        buckets[risk.risk_level(score)] += 1
    # Students with no PACE record yet count as on track.
    buckets["low"] += max(0, len(student_ids) - len(per_student))

    stats.critical = buckets["critical"]
    stats.at_risk = buckets["high"] + buckets["moderate"]
    stats.on_track = buckets["low"]
    stats.risk_distribution = [
        {"name": "On track", "key": "low", "value": buckets["low"]},
        {"name": "Watch", "key": "moderate", "value": buckets["moderate"]},
        {"name": "At risk", "key": "high", "value": buckets["high"]},
        {"name": "Critical", "key": "critical", "value": buckets["critical"]},
    ]

    # ── Average completion per section ────────────────────────────────────────
    by_section: dict[int, list[float]] = {}
    for pace in paces:
        section_id = section_of.get(pace.enrollment_id)
        if section_id is not None:
            by_section.setdefault(section_id, []).append(pace.pace_percent)
    stats.pace_by_section = [
        {
            "section": lookups.sections.get(section_id, f"Section {section_id}"),
            "average": round(sum(values) / len(values), 1),
            "students": len(values),
        }
        for section_id, values in sorted(
            by_section.items(), key=lambda kv: lookups.sections.get(kv[0], "")
        )
    ]

    # ── Average completion per subject ────────────────────────────────────────
    by_subject: dict[str, list[StudentPace]] = {}
    for pace in paces:
        by_subject.setdefault(pace.subject, []).append(pace)
    stats.pace_by_subject = [
        {
            "subject": name,
            "average": round(sum(p.pace_percent for p in rows) / len(rows), 1),
            "behind": sum(1 for p in rows if p.paces_behind > 0),
            "students": len(rows),
        }
        for name, rows in sorted(by_subject.items())
    ]

    # ── Completion against the expected curve, per subject ────────────────────
    stats.trend = [
        {
            "label": name,
            "actual": round(sum(p.pace_percent for p in rows) / len(rows), 1),
            "expected": round(
                sum(
                    risk.pace_percent(p.expected_pace, p.total_paces) for p in rows
                )
                / len(rows),
                1,
            ),
        }
        for name, rows in sorted(by_subject.items())
    ]

    # ── The same three views, read on grades rather than completion ──────────
    # For a school without units these replace the charts above. Standing is
    # the term grade once a record is complete, the coursework banked before.
    grade_by_section: dict[int, list[float]] = {}
    grade_by_subject: dict[str, list[float]] = {}
    grade_by_period: dict[int, list[float]] = {}
    for pace, standing in graded:
        section_id = section_of.get(pace.enrollment_id)
        if section_id is not None:
            grade_by_section.setdefault(section_id, []).append(standing)
        grade_by_subject.setdefault(pace.subject, []).append(standing)
        grade_by_period.setdefault(pace.grading_period or 1, []).append(standing)

    stats.standing_by_section = [
        {
            "section": lookups.sections.get(section_id, f"Section {section_id}"),
            **_standing_summary(values, passing_mark),
        }
        for section_id, values in sorted(
            grade_by_section.items(), key=lambda kv: lookups.sections.get(kv[0], "")
        )
    ]
    stats.standing_by_subject = [
        {"subject": name, **_standing_summary(values, passing_mark)}
        for name, values in sorted(grade_by_subject.items())
    ]
    stats.standing_by_period = [
        {
            "label": f"{stats.grading_period_label} {period}",
            "passing": passing_mark,
            **_standing_summary(values, passing_mark),
        }
        for period, values in sorted(grade_by_period.items())
    ]

    # ── The students who need attention first ─────────────────────────────────
    students = {
        s.student_id: s
        for s in (
            await db.execute(
                select(Student).where(
                    Student.school_id == scope,
                    Student.student_id.in_(student_ids or [0]),
                )
            )
        ).scalars()
    }
    ranked = sorted(warnings, key=lambda w: w.risk_score, reverse=True)[:8]
    stats.top_concerns = [
        {
            "warning_id": w.warning_id,
            "student_id": w.student_id,
            "student_name": (
                students[w.student_id].full_name if w.student_id in students else "—"
            ),
            "section": lookups.sections.get(section_of.get(w.enrollment_id), "—"),
            "subject": w.subject,
            "risk_level": w.risk_level,
            "risk_score": w.risk_score,
            "paces_behind": w.paces_behind,
            "pace_percent": w.pace_percent,
            "standing": w.standing,
            "basis": w.basis,
            "trend": w.trend,
        }
        for w in ranked
    ]

    stats.recent_activity = await _recent_activity(
        db, scope, lookups, tracks_units=stats.tracks_units
    )

    stats.school_year = await _current_year(db, scope)
    return stats


async def _recent_activity(
    db: AsyncSession, scope: int, lookups: Lookups, *, tracks_units: bool, limit: int = 10
) -> list[dict]:
    """The newest actions taken by this school's own staff.

    audit_logs carries no school_id of its own, so the feed is scoped through
    the actor: only actions taken by this school's own staff are shown. The
    super admin's school_id is NULL, so their actions never appear here.
    """
    school_actors = select(Staff.id).where(Staff.school_id == scope)
    logs = (
        await db.execute(
            select(AuditLog)
            .where(AuditLog.admin_id.in_(school_actors))
            .order_by(AuditLog.created_at.desc())
            .limit(limit)
        )
    ).scalars().all()
    return [
        {
            "action": log.action,
            "label": (
                "Class record updated"
                if log.action == "pace_recorded" and not tracks_units
                else ACTIVITY_LABELS.get(log.action, log.action.replace("_", " ").title())
            ),
            "detail": log.detail,
            "actor": lookups.staff.get(str(log.admin_id), "System"),
            "when": _relative(log.created_at),
        }
        for log in logs
    ]


async def _current_year(db: AsyncSession, scope: int) -> Optional[str]:
    year = (
        await db.execute(
            select(SchoolYear).where(
                SchoolYear.school_id == scope,
                SchoolYear.is_current.is_(True),
            )
        )
    ).scalar_one_or_none()
    return year.year if year else None


# A teacher who has not signed in for this long is worth a word.
QUIET_AFTER_DAYS = 14


@router.get("/api/dashboard/admin")
async def admin_dashboard(
    _: Staff = Depends(get_current_admin),
    scope: int = Depends(school_scope),
    db: AsyncSession = Depends(get_db),
):
    """The principal's view: the school and its teachers, not its learners.

    Counts are school-wide totals. The per-teacher figures say how far each
    teacher's classes are graded for the current term: a class is a section
    and subject (see below for how its teacher is found), and a learner
    counts as graded once their record for that term has a term grade.
    """
    school = await db.get(School, scope)
    lookups = await Lookups.load(db, school_id=scope)
    now = datetime.now(timezone.utc)

    teachers = (
        await db.execute(
            select(Staff).where(
                Staff.school_id == scope,
                Staff.role == ROLE_TEACHER,
                Staff.is_approved.is_(True),
            )
        )
    ).scalars().all()
    teachers = [t for t in teachers if getattr(t, "is_active", True)]
    pending = (
        await db.execute(
            select(func.count()).select_from(Staff).where(
                Staff.school_id == scope, Staff.account_status == STATUS_PENDING
            )
        )
    ).scalar_one()
    subjects = {
        s.subject_id: s.subject_name
        for s in (
            await db.execute(
                select(Subject).where(Subject.school_id == scope, Subject.is_active.is_(True))
            )
        ).scalars()
    }
    enrollments = (
        await db.execute(
            select(StudentEnrollment).where(
                StudentEnrollment.school_id == scope,
                StudentEnrollment.is_active.is_(True),
            )
        )
    ).scalars().all()
    learners_in: dict[int, set[int]] = {}
    for e in enrollments:
        learners_in.setdefault(e.section_id, set()).add(e.enrollment_id)

    assignments = (
        await db.execute(select(TeacherAssignment).where(TeacherAssignment.school_id == scope))
    ).scalars().all()
    advised = {a.section_id for a in assignments if a.is_adviser}

    section_of = {e.enrollment_id: e.section_id for e in enrollments}
    records = (
        await db.execute(
            select(
                StudentPace.enrollment_id, StudentPace.subject, StudentPace.grading_period,
                StudentPace.transmuted_grade, StudentPace.recorded_by,
            ).where(StudentPace.school_id == scope)
        )
    ).all()
    # Graded records per (enrolment, subject, term).
    graded_set = {
        (e, s, t or 1) for e, s, t, grade, _by in records if grade is not None
    }
    # The current term is the latest one anyone has started grading.
    term = max((t for _e, _s, t in graded_set), default=1)

    # A class is a section and subject. Which teacher it belongs to is taken,
    # in order, from: an assignment naming that subject; whoever has been
    # recording its grades; a teacher assigned to the section as a whole.
    # Schools set assignments up both ways, and the records never lie about
    # who did the work.
    classes: dict[tuple, Optional[str]] = {}
    for a in assignments:
        if a.subject_id in subjects:
            classes[(a.section_id, subjects[a.subject_id])] = str(a.teacher_id)
    recorders: dict[tuple, dict[str, int]] = {}
    for e, subject, _t, _grade, by in records:
        section_id = section_of.get(e)
        if section_id is None or subject not in subjects.values():
            continue
        tally = recorders.setdefault((section_id, subject), {})
        if by is not None:
            tally[str(by)] = tally.get(str(by), 0) + 1
    section_teacher = {}
    for a in assignments:
        if a.subject_id is None:
            # Prefer the adviser when a section has several general assignments.
            if a.is_adviser or a.section_id not in section_teacher:
                section_teacher[a.section_id] = str(a.teacher_id)
    for key, tally in recorders.items():
        if key in classes:
            continue
        classes[key] = (
            max(tally, key=tally.get) if tally else section_teacher.get(key[0])
        )

    by_teacher: dict[str, dict] = {}
    unstarted = []
    for (section_id, name), teacher_id in classes.items():
        roster = learners_in.get(section_id, set())
        done = sum(1 for e in roster if (e, name, term) in graded_set)
        if teacher_id is not None:
            row = by_teacher.setdefault(teacher_id, {"classes": 0, "graded": 0, "learners": 0})
            row["classes"] += 1
            row["graded"] += done
            row["learners"] += len(roster)
        if roster and done == 0:
            unstarted.append({
                "teacher": lookups.staff.get(teacher_id or "", "No teacher"),
                "section": lookups.sections.get(section_id, f"Section {section_id}"),
                "subject": name,
            })

    teacher_rows = []
    for t in teachers:
        row = by_teacher.get(str(t.id), {"classes": 0, "graded": 0, "learners": 0})
        last = t.last_login_at
        if last is not None and last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        quiet_days = (now - last).days if last else None
        teacher_rows.append({
            "id": str(t.id),
            "name": t.full_name,
            "classes": row["classes"],
            "learners": row["learners"],
            "graded": row["graded"],
            "progress": (
                round(100 * row["graded"] / row["learners"], 1) if row["learners"] else None
            ),
            "last_login": _relative(last) if last else None,
            "quiet": quiet_days is None or quiet_days >= QUIET_AFTER_DAYS,
        })
    # Furthest behind first, teachers with no classes last.
    teacher_rows.sort(key=lambda r: (r["progress"] is None, r["progress"] or 0))

    total_learners = sum(r["learners"] for r in teacher_rows)
    total_graded = sum(r["graded"] for r in teacher_rows)

    return {
        "school_year": await _current_year(db, scope),
        "term": term,
        "term_label": (school.grading_period_label if school else None) or "Quarter",
        "counts": {
            "teachers": len(teachers),
            "pending_approvals": pending,
            "sections": len(lookups.sections),
            "subjects": len(subjects),
            "learners": len({e.student_id for e in enrollments}),
        },
        "grading_progress": (
            round(100 * total_graded / total_learners, 1) if total_learners else None
        ),
        "teachers": teacher_rows,
        "attention": {
            "quiet_teachers": [r["name"] for r in teacher_rows if r["quiet"]],
            "sections_without_adviser": sorted(
                name for sid, name in lookups.sections.items() if sid not in advised
            ),
            "classes_not_started": unstarted[:12],
            "classes_not_started_total": len(unstarted),
        },
        "recent_activity": await _recent_activity(
            db, scope, lookups, tracks_units=bool(school and school.tracks_units)
        ),
    }
