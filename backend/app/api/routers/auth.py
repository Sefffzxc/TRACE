"""Authentication: registration, login, 2FA, refresh, logout, password reset.

The flow mirrors the school's policy:

    register  →  pending  →  admin approves  →  can sign in
    sign in   →  unknown device?  →  6-digit OTP  →  device trusted for 90 days
    5 wrong passwords  →  timed lockout (5 / 15 / 60 min)  →  permanent lock
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    get_current_user,
    get_user_from_refresh_token,
    limit_login,
    limit_otp,
    limit_password_reset,
    limit_register,
)
from app.core.config import settings
from app.core.security import (
    create_access_token,
    create_refresh_token,
    generate_otp,
    generate_reset_token,
    get_password_hash,
    verify_password,
)
from app.db.session import get_db
from app.models.schools import SCHOOL_APPROVED, SCHOOL_PENDING, School
from app.models.staff import (
    ROLE_ADMIN,
    ROLE_TEACHER,
    STATUS_APPROVED,
    STATUS_PENDING,
    OTPCode,
    PasswordReset,
    Staff,
    StaffDevice,
    StaffSession,
)
from app.schemas.staff import (
    ForgotPasswordRequest,
    OTPVerifyRequest,
    ResetPasswordRequest,
    StaffLoginRequest,
    StaffRegisterRequest,
    StaffResponse,
    TokenResponse,
    VerifyResetOTPRequest,
)
from app.services.audit import Action, client_ip, log_event

router = APIRouter(tags=["Authentication"])


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    """SQLite gives back naive datetimes; normalise them to UTC."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _token_bundle(staff: Staff) -> tuple[str, str, datetime]:
    access = create_access_token({"sub": str(staff.id), "role": staff.role})
    refresh = create_refresh_token({"sub": str(staff.id)})
    expires = _now() + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    return access, refresh, expires


async def _issue_session(
    db: AsyncSession, staff: Staff, device: StaffDevice
) -> TokenResponse:
    access, refresh, expires = _token_bundle(staff)
    db.add(
        StaffSession(
            staff_id=staff.id,
            device_id=device.id,
            access_token=access,
            refresh_token=refresh,
            expires_at=expires,
        )
    )
    staff.last_login_at = _now()
    await db.commit()
    await db.refresh(staff)
    return TokenResponse(
        access_token=access,
        refresh_token=refresh,
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        requires_password_change=staff.requires_password_change,
        user_id=str(staff.id),
        user=StaffResponse.model_validate(staff),
    )


# ══════════════════════════════════════════════════════════════════════════════
# Registration
# ══════════════════════════════════════════════════════════════════════════════
@router.post(
    "/api/auth/register",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(limit_register)],
)
async def register(
    payload: StaffRegisterRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Create a teacher account inside an existing school.

    It stays *pending* until that school's own administrator approves it.

    There is no bootstrap here any more. This used to make the very first
    person to register an approved administrator, which was reasonable when the
    system served one school: somebody had to be able to approve everyone else.
    With many schools sharing one deployment it would have handed control of
    the whole system to whoever signed up first. The system operator is now
    created from the command line (`python seed.py --create-superadmin`), and a
    school's first administrator is created with the school itself at
    `POST /api/schools/register`.
    """
    existing = await db.execute(select(Staff).where(Staff.email == payload.email))
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with that email address already exists.",
        )

    # The school must exist and be approved. A pending or suspended school
    # cannot take on staff, and an unapproved one is not visible in the
    # directory the sign-up form reads from in the first place.
    school = await db.get(School, payload.school_id)
    if school is None or school.status != SCHOOL_APPROVED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Please choose a school from the list.",
        )

    staff = Staff(
        school_id=school.school_id,
        email=str(payload.email).lower(),
        password_hash=get_password_hash(payload.password),
        first_name=payload.first_name.strip(),
        middle_name=(payload.middle_name or "").strip() or None,
        last_name=payload.last_name.strip(),
        contact_number=payload.contact_number.strip(),
        role=ROLE_TEACHER,
        employee_no=payload.employee_no,
        department=payload.department,
        account_status=STATUS_PENDING,
        is_approved=False,
    )
    db.add(staff)
    await db.flush()
    await log_event(
        db,
        Action.REGISTER,
        target_user_id=staff.id,
        detail=f"teacher at {school.name} (#{school.school_id})",
        request=request,
    )
    await db.commit()

    return {
        "message": (
            f"Registration received. An administrator at {school.name} will "
            "review your account shortly."
        ),
        "user_id": str(staff.id),
        "school_id": school.school_id,
        "school_name": school.name,
        "account_status": staff.account_status,
        "is_approved": staff.is_approved,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Login
# ══════════════════════════════════════════════════════════════════════════════
@router.post(
    "/api/auth/login",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(limit_login)],
)
async def login(
    payload: StaffLoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Staff).where(Staff.email == str(payload.email).lower())
    )
    staff = result.scalar_one_or_none()

    # Same message whether the email exists or not, so accounts cannot be
    # enumerated from the login form.
    if staff is None:
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    if staff.permanent_lock:
        raise HTTPException(
            status_code=403,
            detail="This account is permanently locked after repeated failed sign-ins. "
            "Please contact your administrator.",
        )

    locked_until = _aware(staff.locked_until)
    if locked_until and locked_until > _now():
        remaining = max(1, int((locked_until - _now()).total_seconds() // 60))
        raise HTTPException(
            status_code=401,
            detail=f"Account temporarily locked. Try again in {remaining} minute(s).",
        )

    if not staff.is_active:
        raise HTTPException(status_code=403, detail="This account has been deactivated.")

    if not staff.is_approved:
        raise HTTPException(
            status_code=403,
            detail="Your account is still awaiting administrator approval.",
        )

    # The school gates every account inside it. A principal whose school is
    # still pending is told so plainly — their account is fine, their school
    # has simply not been approved yet, and saying "awaiting approval" without
    # saying whose would send them to chase the wrong person.
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

    # ── wrong password ────────────────────────────────────────────────────────
    if not verify_password(payload.password, staff.password_hash):
        staff.login_attempts = (staff.login_attempts or 0) + 1

        if staff.login_attempts >= settings.MAX_LOGIN_ATTEMPTS:
            staff.lockout_count = (staff.lockout_count or 0) + 1
            staff.login_attempts = 0

            if staff.lockout_count >= settings.MAX_LOCKOUTS:
                staff.permanent_lock = True
                staff.locked_until = None
                await log_event(
                    db, Action.ACCOUNT_PERM_LOCK, target_user_id=staff.id, request=request
                )
                await db.commit()
                raise HTTPException(
                    status_code=403,
                    detail="This account is permanently locked after repeated failed "
                    "sign-ins. Please contact your administrator.",
                )

            ladder = settings.lockout_ladder
            minutes = ladder[min(staff.lockout_count - 1, len(ladder) - 1)]
            staff.locked_until = _now() + timedelta(minutes=minutes)
            await log_event(
                db,
                Action.ACCOUNT_LOCKED,
                target_user_id=staff.id,
                detail=f"minutes={minutes} tier={staff.lockout_count}",
                request=request,
            )
            await db.commit()
            raise HTTPException(
                status_code=401,
                detail=f"Too many failed attempts. Account locked for {minutes} minute(s) "
                f"(lockout {staff.lockout_count} of {settings.MAX_LOCKOUTS}).",
            )

        await log_event(
            db,
            Action.LOGIN_FAILED,
            target_user_id=staff.id,
            detail=f"attempts={staff.login_attempts}",
            request=request,
        )
        await db.commit()
        left = settings.MAX_LOGIN_ATTEMPTS - staff.login_attempts
        raise HTTPException(
            status_code=401,
            detail=f"Invalid email or password. {left} attempt(s) left before lockout.",
        )

    # ── correct password — clear the lockout ladder ───────────────────────────
    staff.login_attempts = 0
    staff.locked_until = None
    staff.lockout_count = 0

    # ── known device? ─────────────────────────────────────────────────────────
    device_result = await db.execute(
        select(StaffDevice).where(
            StaffDevice.staff_id == staff.id,
            StaffDevice.device_id == payload.device_id,
        )
    )
    device = device_result.scalar_one_or_none()

    requires_2fa = device is None or not device.is_trusted
    if device is not None and device.last_2fa_verified is not None:
        days = (_now() - _aware(device.last_2fa_verified)).days
        if days >= settings.TWO_FACTOR_DAYS_INTERVAL:
            requires_2fa = True

    if requires_2fa:
        code = generate_otp()
        db.add(
            OTPCode(
                staff_id=staff.id,
                code=code,
                purpose="login",
                expires_at=_now() + timedelta(minutes=settings.OTP_EXPIRY_MINUTES),
            )
        )
        await log_event(
            db, Action.OTP_ISSUED, target_user_id=staff.id, detail="login", request=request
        )
        await db.commit()
        # No mail server in this build — the code is printed for the developer
        # and echoed back when DEBUG_OTP is on.
        print(f"[OTP] login code for {staff.email}: {code}", flush=True)
        return TokenResponse(
            requires_2fa=True,
            user_id=str(staff.id),
            debug_otp=code if settings.DEBUG_OTP else None,
        )

    device.ip_address = client_ip(request)
    device.device_name = payload.device_name or device.device_name
    await log_event(
        db, Action.LOGIN_SUCCESS, actor_id=staff.id, detail="trusted device", request=request
    )
    return await _issue_session(db, staff, device)


# ══════════════════════════════════════════════════════════════════════════════
# 2FA verification
# ══════════════════════════════════════════════════════════════════════════════
@router.post(
    "/api/auth/verify-otp",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(limit_otp)],
)
async def verify_otp(
    payload: OTPVerifyRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    staff = (
        await db.execute(select(Staff).where(Staff.id == payload.user_id))
    ).scalar_one_or_none()
    if staff is None:
        raise HTTPException(status_code=404, detail="Account not found.")

    otp = (
        await db.execute(
            select(OTPCode)
            .where(
                OTPCode.staff_id == payload.user_id,
                OTPCode.purpose == "login",
                OTPCode.is_used.is_(False),
            )
            .order_by(OTPCode.created_at.desc())
        )
    ).scalars().first()

    if otp is None:
        raise HTTPException(
            status_code=400, detail="No active code. Please sign in again to get a new one."
        )
    if _aware(otp.expires_at) < _now():
        raise HTTPException(
            status_code=400, detail="That code has expired. Please request a new one."
        )
    if otp.attempts >= settings.OTP_MAX_ATTEMPTS:
        otp.is_used = True
        await db.commit()
        raise HTTPException(
            status_code=429, detail="Too many incorrect codes. Please sign in again."
        )

    if otp.code != payload.code:
        otp.attempts += 1
        await log_event(
            db, Action.OTP_FAILED, target_user_id=staff.id, request=request
        )
        await db.commit()
        left = settings.OTP_MAX_ATTEMPTS - otp.attempts
        raise HTTPException(
            status_code=400, detail=f"Incorrect code. {left} attempt(s) remaining."
        )

    otp.is_used = True

    device = (
        await db.execute(
            select(StaffDevice).where(
                StaffDevice.staff_id == staff.id,
                StaffDevice.device_id == payload.device_id,
            )
        )
    ).scalar_one_or_none()

    if device is None:
        device = StaffDevice(
            staff_id=staff.id,
            device_id=payload.device_id,
            device_name=payload.device_name,
            ip_address=client_ip(request),
            user_agent=request.headers.get("user-agent"),
            is_trusted=True,
            last_2fa_verified=_now(),
        )
        db.add(device)
        await db.flush()
    else:
        device.is_trusted = True
        device.last_2fa_verified = _now()
        device.ip_address = client_ip(request)

    await log_event(db, Action.OTP_VERIFIED, actor_id=staff.id, request=request)
    await log_event(db, Action.LOGIN_SUCCESS, actor_id=staff.id, detail="after 2FA")
    return await _issue_session(db, staff, device)


# ══════════════════════════════════════════════════════════════════════════════
# Refresh / logout
# ══════════════════════════════════════════════════════════════════════════════
@router.post("/api/auth/refresh", response_model=TokenResponse)
async def refresh_session(
    staff: Staff = Depends(get_user_from_refresh_token),
    db: AsyncSession = Depends(get_db),
):
    access, refresh, expires = _token_bundle(staff)
    session = (
        await db.execute(
            select(StaffSession)
            .where(StaffSession.staff_id == staff.id, StaffSession.is_active.is_(True))
            .order_by(StaffSession.created_at.desc())
        )
    ).scalars().first()
    if session is not None:
        session.access_token = access
        session.refresh_token = refresh
        session.expires_at = expires
    await db.commit()
    return TokenResponse(
        access_token=access,
        refresh_token=refresh,
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user_id=str(staff.id),
        user=StaffResponse.model_validate(staff),
    )


@router.post("/api/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    staff: Staff = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    sessions = (
        await db.execute(
            select(StaffSession).where(
                StaffSession.staff_id == staff.id, StaffSession.is_active.is_(True)
            )
        )
    ).scalars().all()
    for session in sessions:
        session.is_active = False
    await log_event(db, Action.LOGOUT, actor_id=staff.id, request=request)
    await db.commit()


# ══════════════════════════════════════════════════════════════════════════════
# Password reset
# ══════════════════════════════════════════════════════════════════════════════
@router.post(
    "/api/auth/forgot-password",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(limit_password_reset)],
)
async def forgot_password(
    payload: ForgotPasswordRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Send a reset code. The reply never reveals whether the email exists."""
    generic = {
        "message": "If that email is registered, a 6-digit reset code has been sent to it."
    }

    staff = (
        await db.execute(select(Staff).where(Staff.email == str(payload.email).lower()))
    ).scalar_one_or_none()
    if staff is None:
        return generic

    record = (
        await db.execute(
            select(PasswordReset)
            .where(PasswordReset.staff_id == staff.id)
            .order_by(PasswordReset.created_at.desc())
        )
    ).scalars().first()

    now = _now()
    if record is not None and _aware(record.window_start) > now - timedelta(hours=1):
        if record.request_count >= settings.RESET_MAX_REQUESTS_PER_HOUR:
            raise HTTPException(
                status_code=429,
                detail="Too many reset requests. Please try again in an hour.",
            )
        record.request_count += 1
    else:
        record = PasswordReset(staff_id=staff.id, request_count=1, window_start=now)
        db.add(record)

    code = generate_otp()
    db.add(
        OTPCode(
            staff_id=staff.id,
            code=code,
            purpose="password-reset",
            expires_at=now + timedelta(minutes=settings.OTP_EXPIRY_MINUTES),
        )
    )
    await log_event(
        db, Action.PASSWORD_RESET_REQUESTED, target_user_id=staff.id, request=request
    )
    await db.commit()
    print(f"[OTP] password reset code for {staff.email}: {code}", flush=True)

    response = dict(generic)
    if settings.DEBUG_OTP:
        response["debug_otp"] = code
    return response


@router.post("/api/auth/verify-reset-otp", dependencies=[Depends(limit_otp)])
async def verify_reset_otp(
    payload: VerifyResetOTPRequest, db: AsyncSession = Depends(get_db)
):
    """Exchange a valid reset code for a short-lived reset token."""
    staff = (
        await db.execute(select(Staff).where(Staff.email == str(payload.email).lower()))
    ).scalar_one_or_none()
    if staff is None:
        raise HTTPException(status_code=400, detail="Invalid or expired code.")

    otp = (
        await db.execute(
            select(OTPCode)
            .where(
                OTPCode.staff_id == staff.id,
                OTPCode.purpose == "password-reset",
                OTPCode.is_used.is_(False),
            )
            .order_by(OTPCode.created_at.desc())
        )
    ).scalars().first()

    if otp is None or _aware(otp.expires_at) < _now():
        raise HTTPException(status_code=400, detail="Invalid or expired code.")
    if otp.code != payload.code:
        otp.attempts += 1
        await db.commit()
        raise HTTPException(status_code=400, detail="Invalid or expired code.")

    otp.is_used = True
    token = generate_reset_token()
    record = PasswordReset(
        staff_id=staff.id,
        reset_token=token,
        token_expires=_now() + timedelta(minutes=settings.RESET_TOKEN_EXPIRE_MINUTES),
    )
    db.add(record)
    await db.commit()
    return {
        "token": token,
        "expires_in": settings.RESET_TOKEN_EXPIRE_MINUTES * 60,
        "message": "Code verified. You may now set a new password.",
    }


@router.post("/api/auth/reset-password")
async def reset_password(
    payload: ResetPasswordRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    record = (
        await db.execute(
            select(PasswordReset).where(PasswordReset.reset_token == payload.token)
        )
    ).scalar_one_or_none()

    if (
        record is None
        or record.used_at is not None
        or record.token_expires is None
        or _aware(record.token_expires) < _now()
    ):
        raise HTTPException(
            status_code=400,
            detail="This reset link has expired. Please start the reset again.",
        )

    staff = (
        await db.execute(select(Staff).where(Staff.id == record.staff_id))
    ).scalar_one_or_none()
    if staff is None:
        raise HTTPException(status_code=404, detail="Account not found.")

    if verify_password(payload.new_password, staff.password_hash):
        raise HTTPException(
            status_code=400,
            detail="Your new password must be different from the current one.",
        )

    staff.password_hash = get_password_hash(payload.new_password)
    staff.requires_password_change = False
    staff.login_attempts = 0
    staff.locked_until = None
    staff.lockout_count = 0
    staff.permanent_lock = False
    record.used_at = _now()
    record.reset_token = None

    await log_event(
        db, Action.PASSWORD_RESET_COMPLETED, target_user_id=staff.id, request=request
    )
    await db.commit()
    return {"message": "Password updated. You can now sign in with your new password."}


# ══════════════════════════════════════════════════════════════════════════════
# Convenience
# ══════════════════════════════════════════════════════════════════════════════
@router.get("/api/auth/me", response_model=StaffResponse)
async def whoami(staff: Staff = Depends(get_current_user)):
    return StaffResponse.model_validate(staff)
