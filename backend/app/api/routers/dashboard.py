"""Everything the dashboard draws, computed server-side in one request."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, school_scope
from app.db.session import get_db
from app.models.academic import GradeLevel, SchoolYear, Section, Subject
from app.models.staff import (
    ROLE_ADMIN,
    ROLE_TEACHER,
    STATUS_PENDING,
    AuditLog,
    Staff,
)
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

    if paces:
        stats.average_pace = round(sum(p.pace_percent for p in paces) / len(paces), 1)
        stats.average_attendance = round(
            sum(p.attendance for p in paces) / len(paces), 1
        )

    # ── Risk distribution, one entry per student (worst subject wins) ─────────
    per_student: dict[int, float] = {}
    for pace in paces:
        score = risk.risk_score(
            pace.paces_behind, pace.pace_percent, pace.attendance, pace.total_paces
        )
        per_student[pace.student_id] = max(per_student.get(pace.student_id, 0.0), score)

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
            "trend": w.trend,
        }
        for w in ranked
    ]

    # ── Recent activity feed ──────────────────────────────────────────────────
    # audit_logs carries no school_id of its own, so the feed is scoped through
    # the actor: only actions taken by this school's own staff are shown. The
    # super admin's school_id is NULL, so their actions never appear here.
    school_actors = select(Staff.id).where(Staff.school_id == scope)
    logs = (
        await db.execute(
            select(AuditLog)
            .where(AuditLog.admin_id.in_(school_actors))
            .order_by(AuditLog.created_at.desc())
            .limit(10)
        )
    ).scalars().all()
    stats.recent_activity = [
        {
            "action": log.action,
            "label": ACTIVITY_LABELS.get(log.action, log.action.replace("_", " ").title()),
            "detail": log.detail,
            "actor": lookups.staff.get(str(log.admin_id), "System"),
            "when": _relative(log.created_at),
        }
        for log in logs
    ]

    year = (
        await db.execute(
            select(SchoolYear).where(
                SchoolYear.school_id == scope,
                SchoolYear.is_current.is_(True),
            )
        )
    ).scalar_one_or_none()
    stats.school_year = year.year if year else None
    return stats
