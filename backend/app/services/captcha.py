"""Verify Cloudflare Turnstile tokens on the API server.

Only Siteverify's authenticated reply can satisfy a challenge. A successful
reply must belong to an explicitly allowed hostname and to the intended form.
Provider bodies, submitted tokens, and the private key never enter errors or
application logs.
"""
from __future__ import annotations

import httpx
from fastapi import HTTPException

from app.core.config import settings


_SITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
_ACTIONS = frozenset({"register_teacher", "register_school", "login"})
_PROVIDER_ERRORS = frozenset({
    "missing-input-secret", "invalid-input-secret", "bad-request", "internal-error",
})


def _failure(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={
        "code": code, "message": message, "captcha_required": True,
    })


def _unavailable() -> HTTPException:
    return _failure(
        503, "captcha_unavailable",
        "Human verification is temporarily unavailable. Please try again shortly.",
    )


def _invalid() -> HTTPException:
    return _failure(
        400, "captcha_invalid",
        "Human verification failed or expired. Please complete it again.",
    )


async def verify_captcha(token: str | None, *, action: str) -> None:
    """Require a valid token for ``action`` when CAPTCHA is enabled.

    Failures are safe public HTTP errors. No local token shortcuts or successful
    fallback exist when verification is enabled, including on provider outages.
    """
    if not settings.CAPTCHA_ENABLED:
        return

    secret = settings.TURNSTILE_SECRET_KEY.strip()
    site_key = settings.TURNSTILE_SITE_KEY.strip()
    allowed_hosts = {
        value.strip().lower()
        for value in settings.TURNSTILE_ALLOWED_HOSTNAMES.split(",")
        if value.strip()
    }
    if not secret or not site_key or not allowed_hosts or action not in _ACTIONS:
        raise _unavailable()

    if token is None or (isinstance(token, str) and not token.strip()):
        raise _failure(
            400, "captcha_required", "Please complete the human verification.",
        )
    if not isinstance(token, str) or len(token) > 2048:
        raise _invalid()

    try:
        async with httpx.AsyncClient(timeout=settings.CAPTCHA_TIMEOUT_SECONDS) as client:
            response = await client.post(
                _SITEVERIFY_URL, data={"secret": secret, "response": token},
            )
    except httpx.HTTPError:
        # HTTP exceptions can contain request data. Never forward their text.
        raise _unavailable() from None

    if not 200 <= response.status_code < 300:
        raise _unavailable()
    try:
        result = response.json()
    except ValueError:
        raise _unavailable() from None
    if not isinstance(result, dict) or type(result.get("success")) is not bool:
        raise _unavailable()

    if result["success"] is not True:
        codes = result.get("error-codes", [])
        if not isinstance(codes, list) or any(not isinstance(code, str) for code in codes):
            raise _unavailable()
        if _PROVIDER_ERRORS.intersection(codes):
            raise _unavailable()
        raise _invalid()

    hostname = result.get("hostname")
    if (
        not isinstance(hostname, str)
        or hostname.lower() not in allowed_hosts
        or result.get("action") != action
    ):
        raise _invalid()
