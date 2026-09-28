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
from app.models.staff import ROLE_ADMIN, Staff

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
    if current.role != ROLE_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator access is required for this action.",
        )
    return current


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
