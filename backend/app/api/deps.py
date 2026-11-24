"""Shared FastAPI dependencies: current user, role guards, rate limiting."""
from __future__ import annotations

import hashlib
import json
import math
import secrets
import threading
import time
import uuid
from collections import OrderedDict, deque
from dataclasses import dataclass, field

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_token, required_password_proof
from app.core.config import settings
from app.db.session import get_db
from app.models.schools import SCHOOL_APPROVED, SCHOOL_PENDING, School
from app.models.email_corrections import EmailCorrectionChallenge
from app.models.staff import ROLE_ADMIN, ROLE_SUPER_ADMIN, STATUS_BLOCKED, STATUS_REJECTED, Staff, StaffSession

bearer_scheme = HTTPBearer(auto_error=False)


async def require_account_access(staff: Staff, db: AsyncSession) -> None:
    """Check current account and school decisions after verifying identity."""
    if staff.account_status == STATUS_BLOCKED:
        raise HTTPException(
            status_code=403,
            detail="This application has been blocked. Please contact the administrator to request a review.",
        )
    if staff.account_status == STATUS_REJECTED:
        detail = "Your account registration has been rejected."
        if staff.rejection_reason:
            detail += f" Reason: {staff.rejection_reason}"
        contact = "the system operator" if staff.role == ROLE_ADMIN else "your school administrator"
        raise HTTPException(status_code=403, detail=f"{detail} Please contact {contact}.")
    if not staff.is_active:
        raise HTTPException(status_code=403, detail="This account has been deactivated.")
    if not staff.is_approved:
        raise HTTPException(
            status_code=403, detail="Your account is still awaiting administrator approval.",
        )

    # Recheck the school's decision for both new sign-ins and existing tokens.
    if staff.school_id is not None:
        school = await db.get(School, staff.school_id)
        if school is None:
            raise HTTPException(status_code=403, detail="This school no longer exists.")
        if school.status == SCHOOL_PENDING:
            raise HTTPException(
                status_code=403,
                detail=(
                    f"{school.name} is still awaiting approval by the system "
                    "operator. You will be able to sign in once it is approved."
                ),
            )
        if school.status != SCHOOL_APPROVED:
            raise HTTPException(
                status_code=403,
                detail=(
                    f"Access to {school.name} has been suspended."
                    + (f" Reason: {school.rejection_reason}" if school.rejection_reason else "")
                ),
            )


async def _staff_from_token(
    token: str, db: AsyncSession, expected_type: str = "access"
) -> Staff:
    payload = decode_token(token)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired or token invalid. Please sign in again.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if payload.get("type") != expected_type:
        raise HTTPException(status_code=401, detail="Wrong token type.")

    subject = payload.get("sub")
    try:
        staff_id = uuid.UUID(str(subject))
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="Malformed token.")

    token_column = StaffSession.access_token if expected_type == "access" else StaffSession.refresh_token
    revoked = select(StaffSession.id).where(
        StaffSession.staff_id == staff_id, token_column == token,
        StaffSession.is_active.is_(False),
    ).exists()
    active = select(StaffSession.id).where(
        StaffSession.staff_id == staff_id, token_column == token,
        StaffSession.is_active.is_(True),
    ).exists()
    corrected = select(EmailCorrectionChallenge.id).where(
        EmailCorrectionChallenge.staff_id == staff_id,
        EmailCorrectionChallenge.completed_at.is_not(None),
    ).exists()
    result = await db.execute(select(Staff, revoked, active, corrected).where(Staff.id == staff_id))
    row = result.one_or_none()
    if row is None:
        raise HTTPException(status_code=401, detail="Account no longer exists.")
    staff, has_revoked_session, has_active_session, has_email_correction = row
    await require_account_access(staff, db)
    if (has_revoked_session or has_email_correction) and not has_active_session:
        raise HTTPException(status_code=401, detail="This session has ended. Please sign in again.")
    return staff


def require_temporary_password_session(staff: Staff, token: str) -> None:
    """A pre-reset session cannot replace proof of the temporary password."""
    if not staff.requires_password_change:
        return
    payload = decode_token(token) or {}
    proof = payload.get("password_change_proof")
    expected = required_password_proof(staff.id, staff.password_hash)
    if not isinstance(proof, str) or not secrets.compare_digest(proof, expected):
        raise HTTPException(
            status_code=401,
            detail="Please sign in again using the temporary password issued by your administrator.",
        )


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> Staff:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    staff = await _staff_from_token(credentials.credentials, db, "access")
    password_change_routes = {
        ("GET", "/api/auth/me"),
        ("GET", "/api/staff/me"),
        ("POST", "/api/staff/me/password"),
        ("POST", "/api/staff/me/required-password"),
        ("POST", "/api/auth/logout"),
    }
    if staff.requires_password_change and (request.method, request.url.path) not in password_change_routes:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "password_change_required",
                "message": "Change your temporary password before accessing TRACE.",
            },
        )
    return staff


async def get_current_admin(current: Staff = Depends(get_current_user)) -> Staff:
    """A school's own administrator. NOT the super admin — see `school_scope`."""
    if current.role != ROLE_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator access is required for this action.",
        )
    return current


async def get_super_admin(current: Staff = Depends(get_current_user)) -> Staff:
    """The operator of the deployment. Manages schools and accounts only."""
    if current.role != ROLE_SUPER_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This action is restricted to the system operator.",
        )
    return current


# ══════════════════════════════════════════════════════════════════════════════
# The tenant boundary
# ══════════════════════════════════════════════════════════════════════════════
async def school_scope(
    request: Request,
    current: Staff = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> int:
    """The school whose records this request may touch — and the one rule that
    keeps the super admin out of them.

    Every endpoint that reads or writes a student's data depends on this and
    filters by what it returns. That makes the boundary a property of the
    system rather than a habit: an endpoint that forgets to scope its query
    will not compile into something that silently leaks, because it has no
    school id to query with in the first place.

    The super admin is refused here, deliberately and by design. They approve
    schools, they approve and deactivate accounts, they can see how many
    students a school has — and they cannot open a single student's record,
    grades, attendance or risk flag in any school. A system operator does not
    need that access to do their job, and the people whose records these are
    never agreed to it.

    This is the answer to "so the developers can read every child's file?":
    no, and here is the function that stops it.
    """
    if current.role == ROLE_SUPER_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "The system operator account cannot access student records. "
                "It manages schools and accounts only. Sign in with a school "
                "account to work with students."
            ),
        )
    if current.school_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account is not attached to a school.",
        )
    await _check_school_year_header(request, db, int(current.school_id))
    return int(current.school_id)


async def _check_school_year_header(request: Request, db: AsyncSession, school_id: int) -> None:
    """Stop a stale browser from saving one year's draft into another year.

    The header is a version check, never authorization to choose a school or
    gain access to an archived year. Requests without it still use the normal
    current-year filters. Short shared locks serialize academic operations
    against the principal's year switch on PostgreSQL.
    """
    raw = request.headers.get("X-School-Year-Id")
    if raw is None:
        return
    path = request.url.path
    prefixes = (
        "/api/students", "/api/enrollments", "/api/paces", "/api/class-record",
        "/api/early-warnings", "/api/ai", "/api/dashboard", "/api/grade-levels",
        "/api/sections", "/api/subjects", "/api/section-advisers",
        "/api/teacher-assignments", "/api/my-classes", "/api/class-import",
        "/api/import", "/api/data-quality-logs",
        "/api/staff/teachers",
    )
    year_action = path.startswith("/api/school-years") and request.method not in ("GET", "HEAD", "OPTIONS")
    if not year_action and not any(path == p or path.startswith(p + "/") for p in prefixes):
        return
    try:
        expected = int(raw)
        if expected < 1:
            raise ValueError
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid school-year version. Refresh this page.") from None
    from app.models.academic import SchoolYear

    if not year_action:
        # Match the year resolver's lock order: school first, then year.
        # A principal switching years takes the school's exclusive lock.
        await db.execute(select(School.school_id).where(
            School.school_id == school_id,
        ).with_for_update(read=True, key_share=True))
    stmt = select(SchoolYear.school_year_id).where(
        SchoolYear.school_id == school_id, SchoolYear.is_current.is_(True),
    )
    if not year_action:
        stmt = stmt.with_for_update(read=True)
    rows = (await db.scalars(stmt)).all()
    selected = rows[0] if len(rows) == 1 else None
    if expected != selected:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "school_year_changed",
                "message": "The current school year has changed. Reload this page before continuing.",
                "current_school_year_id": selected,
            },
        )


async def school_admin_scope(
    current: Staff = Depends(get_current_user),
) -> int:
    """Same boundary, for actions only a school's own administrator may take."""
    if current.role == ROLE_SUPER_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "The system operator account cannot manage a school's records. "
                "It manages schools and accounts only."
            ),
        )
    if current.role != ROLE_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator access is required for this action.",
        )
    if current.school_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account is not attached to a school.",
        )
    return int(current.school_id)


async def get_user_from_refresh_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> Staff:
    if credentials is None:
        raise HTTPException(status_code=401, detail="Refresh token missing.")
    return await _staff_from_token(credentials.credentials, db, "refresh")


# ── A small in-process rate limiter ───────────────────────────────────────────
# These counters are per process. A deployment with multiple API workers needs
# a shared store (for example Redis) before relying on a deployment-wide limit.
@dataclass
class _RateBucket:
    window_seconds: int
    timestamps: deque[float] = field(default_factory=deque)


_BUCKETS: OrderedDict[str, _RateBucket] = OrderedDict()
_BUCKET_LOCK = threading.Lock()
_MAX_RATE_BUCKETS = 10000


def _rate_limit_error(retry_seconds: float) -> HTTPException:
    retry_in = max(1, math.ceil(retry_seconds))
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=f"Too many requests. Please try again in {retry_in} second(s).",
        headers={"Retry-After": str(retry_in)},
    )


def _rate_limit_many(rules: list[tuple[str, int, int]]) -> None:
    """Reserve account and network capacity together, without extending a ban.

    Rejected requests add no timestamps. The reported delay therefore describes
    the current window rather than starting a fresh wait whenever someone tries
    again. A lock also makes the check and reservation atomic across threads.
    """
    with _BUCKET_LOCK:
        now = time.monotonic()
        # Lazy cleanup bounds both memory and the work done on a normal request.
        # Never evict an active counter to make room for a new attacker identity.
        missing_keys = {key for key, _, _ in rules if key not in _BUCKETS}
        if len(_BUCKETS) >= _MAX_RATE_BUCKETS or len(_BUCKETS) + len(missing_keys) > _MAX_RATE_BUCKETS:
            for key, bucket in list(_BUCKETS.items()):
                while bucket.timestamps and bucket.timestamps[0] <= now - bucket.window_seconds:
                    bucket.timestamps.popleft()
                if not bucket.timestamps:
                    del _BUCKETS[key]

        pending = []
        retry_in = 0.0
        for key, limit, window_seconds in rules:
            bucket = _BUCKETS.get(key)
            if bucket is None:
                bucket = _RateBucket(window_seconds)
            while bucket.timestamps and bucket.timestamps[0] <= now - window_seconds:
                bucket.timestamps.popleft()
            if len(bucket.timestamps) >= limit:
                retry_in = max(retry_in, bucket.timestamps[0] + window_seconds - now)
            pending.append((key, bucket))
        if retry_in > 0:
            raise _rate_limit_error(retry_in)

        missing = sum(key not in _BUCKETS for key, _ in pending)
        if len(_BUCKETS) + missing > _MAX_RATE_BUCKETS:
            # Capacity exhaustion fails closed; active protections stay intact.
            earliest_expiry = min(
                bucket.timestamps[-1] + bucket.window_seconds for bucket in _BUCKETS.values()
            )
            raise _rate_limit_error(earliest_expiry - now)
        for key, bucket in pending:
            bucket.timestamps.append(now)
            _BUCKETS[key] = bucket
            _BUCKETS.move_to_end(key)


def rate_limit(key: str, limit: int, window_seconds: int) -> None:
    _rate_limit_many([(key, limit, window_seconds)])


def limiter(prefix: str, limit: int, window_seconds: int, *, ip_limit_setting: str | None = None):
    """Limit one account tightly and a shared school network more generously."""

    async def _dependency(request: Request) -> None:
        # Uvicorn applies forwarded addresses only from its trusted proxy. Raw
        # X-Forwarded-For / X-Real-IP headers are never anti-abuse identities.
        ip = request.client.host if request.client else "unknown"
        ip_limit = getattr(settings, ip_limit_setting) if ip_limit_setting else limit
        rules = [(f"{prefix}:ip:{ip}", ip_limit, window_seconds)]
        if ip_limit_setting:
            try:
                payload = await request.json()
            except (json.JSONDecodeError, UnicodeDecodeError):
                payload = None
            if isinstance(payload, dict):
                # Each route has one authoritative identity. Extra JSON fields
                # must not let an attacker rotate a different account key.
                uses_user_id = prefix == "otp" and request.url.path.endswith("/verify-otp")
                value = payload.get("user_id" if uses_user_id else "email")
                identity = None
                if not uses_user_id and isinstance(value, str) and 0 < len(value.strip()) <= 320:
                    identity = f"email:{value.strip().lower()}"
                elif uses_user_id and isinstance(value, str):
                    try:
                        identity = f"user:{uuid.UUID(value)}"
                    except ValueError:
                        pass
                if identity:
                    digest = hashlib.sha256(identity.encode()).hexdigest()
                    rules.append((f"{prefix}:account:{digest}", limit, window_seconds))
        _rate_limit_many(rules)

    return _dependency


limit_login = limiter("login", 10, 60, ip_limit_setting="AUTH_LOGIN_IP_LIMIT")
limit_otp = limiter("otp", 10, 60, ip_limit_setting="AUTH_OTP_IP_LIMIT")
limit_password_reset = limiter("reset", 5, 300, ip_limit_setting="AUTH_RESET_IP_LIMIT")
limit_register = limiter("register", 5, 300, ip_limit_setting="AUTH_REGISTER_IP_LIMIT")
limit_resubmission_request = limiter("resubmission-request", 5, 300, ip_limit_setting="AUTH_REGISTER_IP_LIMIT")
limit_resubmission_verify = limiter("resubmission-verify", 10, 60, ip_limit_setting="AUTH_OTP_IP_LIMIT")
limit_resubmission_submit = limiter("resubmission-submit", 10, 60, ip_limit_setting="AUTH_REGISTER_IP_LIMIT")
