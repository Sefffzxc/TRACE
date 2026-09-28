"""Password hashing, JWT issuing/decoding, OTP generation, password policy."""
from __future__ import annotations

import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
from jose import JWTError, jwt

from app.core.config import settings

# ── Passwords ─────────────────────────────────────────────────────────────────
# bcrypt is used directly (no passlib) so there is one less moving part, and
# bcrypt's 72-byte input limit is handled explicitly.
_BCRYPT_MAX_BYTES = 72


def _prepare(password: str) -> bytes:
    return password.encode("utf-8")[:_BCRYPT_MAX_BYTES]


def get_password_hash(password: str) -> str:
    return bcrypt.hashpw(_prepare(password), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(_prepare(plain), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


PASSWORD_RULES = (
    "Password must be at least 8 characters and include an uppercase letter, "
    "a lowercase letter, a number and a special character."
)


def validate_password_strength(password: str) -> tuple[bool, str]:
    """Return ``(ok, message)`` for the school's password policy."""
    if len(password) < 8:
        return False, "Password must be at least 8 characters long."
    if not re.search(r"[A-Z]", password):
        return False, "Password must contain at least one uppercase letter."
    if not re.search(r"[a-z]", password):
        return False, "Password must contain at least one lowercase letter."
    if not re.search(r"\d", password):
        return False, "Password must contain at least one number."
    if not re.search(r"[^A-Za-z0-9]", password):
        return False, "Password must contain at least one special character."
    return True, "Password is strong."


# ── Tokens ────────────────────────────────────────────────────────────────────
def _create_token(data: dict[str, Any], expires_delta: timedelta, token_type: str) -> str:
    payload = data.copy()
    now = datetime.now(timezone.utc)
    payload.update({"exp": now + expires_delta, "iat": now, "type": token_type})
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def create_access_token(data: dict[str, Any]) -> str:
    return _create_token(
        data, timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES), "access"
    )


def create_refresh_token(data: dict[str, Any]) -> str:
    return _create_token(
        data, timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS), "refresh"
    )


def decode_token(token: str) -> dict[str, Any] | None:
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except JWTError:
        return None


# ── One-time codes ────────────────────────────────────────────────────────────
def generate_otp(length: int = 6) -> str:
    return "".join(secrets.choice("0123456789") for _ in range(length))


def generate_reset_token() -> str:
    return secrets.token_urlsafe(32)


def generate_temp_password() -> str:
    """A policy-compliant temporary password for admin-forced resets."""
    upper = secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ")
    lower = "".join(secrets.choice("abcdefghijkmnpqrstuvwxyz") for _ in range(5))
    digits = "".join(secrets.choice("23456789") for _ in range(3))
    special = secrets.choice("!@#$%&*")
    return f"{upper}{lower}{digits}{special}"
