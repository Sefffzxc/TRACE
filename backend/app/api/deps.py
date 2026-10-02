"""Shared FastAPI dependencies: current user, role guards, rate limiting."""
from __future__ import annotations

import time
import uuid
from collections import defaultdict, deque
from typing import Deque, Dict

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_token
from app.db.session import get_db
from app.models.schools import SCHOOL_APPROVED, SCHOOL_PENDING, School
from app.models.staff import ROLE_ADMIN, ROLE_SUPER_ADMIN, Staff

bearer_scheme = HTTPBearer(auto_error=False)


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

    result = await db.execute(select(Staff).where(Staff.id == staff_id))
    staff = result.scalar_one_or_none()
    if staff is None:
        raise HTTPException(status_code=401, detail="Account no longer exists.")
    if not staff.is_active:
        raise HTTPException(status_code=403, detail="This account has been deactivated.")
    if not staff.is_approved:
        raise HTTPException(status_code=403, detail="Account is pending admin approval.")

    # A school's approval gates every account inside it. Checked on each
    # request rather than only at sign-in, so that when the operator suspends a
    # school its staff lose access immediately instead of whenever their token
    # happens to expire.
    if staff.school_id is not None:
        school = await db.get(School, staff.school_id)
        if school is None:
            raise HTTPException(status_code=403, detail="This school no longer exists.")
        if school.status == SCHOOL_PENDING:
            raise HTTPException(
                status_code=403,
                detail="This school is still awaiting approval by the system operator.",
            )
        if school.status != SCHOOL_APPROVED:
            raise HTTPException(
                status_code=403,
                detail=(
                    f"Access to {school.name} has been suspended."
                    + (f" Reason: {school.rejection_reason}" if school.rejection_reason else "")
                ),
            )
    return staff


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> Staff:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return await _staff_from_token(credentials.credentials, db, "access")


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
async def school_scope(current: Staff = Depends(get_current_user)) -> int:
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
    return int(current.school_id)


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
# Enough for a single-instance deployment; swap for Redis if the API is scaled
# horizontally.
_BUCKETS: Dict[str, Deque[float]] = defaultdict(deque)


def rate_limit(key: str, limit: int, window_seconds: int) -> None:
    now = time.monotonic()
    bucket = _BUCKETS[key]
    while bucket and now - bucket[0] > window_seconds:
        bucket.popleft()
    if len(bucket) >= limit:
        retry_in = int(window_seconds - (now - bucket[0])) + 1
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many requests. Please try again in {retry_in} second(s).",
            headers={"Retry-After": str(retry_in)},
        )
    bucket.append(now)


def limiter(prefix: str, limit: int, window_seconds: int):
    """Build a dependency that rate-limits by client IP."""

    async def _dependency(request: Request) -> None:
        forwarded = request.headers.get("x-forwarded-for")
        ip = (
            forwarded.split(",")[0].strip()
            if forwarded
            else (request.client.host if request.client else "unknown")
        )
        rate_limit(f"{prefix}:{ip}", limit, window_seconds)

    return _dependency


limit_login = limiter("login", 10, 60)
limit_otp = limiter("otp", 10, 60)
limit_password_reset = limiter("reset", 5, 300)
limit_register = limiter("register", 5, 300)
