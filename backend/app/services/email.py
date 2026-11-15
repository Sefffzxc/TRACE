"""Deliver branded transactional emails through Resend.

The HTTP API uses the existing httpx dependency. Configuration belongs only on
the API server; neither the email credential nor OTPs belong in frontend builds.
"""
from __future__ import annotations

import asyncio
from base64 import b64encode
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from functools import lru_cache
from html import escape
import logging
import math
from pathlib import Path
import threading
import time
from typing import Literal

import httpx

from app.core.config import settings


log = logging.getLogger("trace.email")
OTPPurpose = Literal["login", "password-reset", "resubmission"]
AccountDecision = Literal["approved", "rejected", "reopened", "blocked"]
_PURPOSES = {"login": "sign-in", "password-reset": "password reset", "resubmission": "application correction"}
_LOGO_CONTENT_ID = "trace-logo"
_LOGO_PATH = Path(__file__).resolve().parent.parent / "assets" / "email" / "trace-logo.png"


class EmailDeliveryError(Exception):
    """Delivery failed; messages must never contain credentials or email bodies."""


class _EmailPacer:
    """Pace starts across concurrent sends, without holding a lock over I/O.

    A threading lock protects a few monotonic timestamps, so this scheduler is
    safe across test/event loops and never binds a global asyncio primitive to
    an expired loop. Each waiter rechecks shared cooldowns before starting.
    Counters are per worker; use a shared queue when scaling to more workers.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._next_allowed = 0.0
        self._cooldown_until = 0.0

    def defer(self, seconds: float) -> None:
        with self._lock:
            self._cooldown_until = max(self._cooldown_until, time.monotonic() + seconds)

    async def wait_turn(self, deadline: float) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                start_at = max(now, self._next_allowed, self._cooldown_until)
                if now >= deadline or start_at > deadline:
                    log.warning("Email send queue reached its bounded wait limit.")
                    raise EmailDeliveryError("Email delivery is temporarily unavailable.")
                delay = start_at - now
                if delay <= 0:
                    self._next_allowed = now + 1.0 / settings.EMAIL_SEND_RATE_PER_SECOND
                    return
            await asyncio.sleep(delay)


_EMAIL_PACER = _EmailPacer()
_MAX_RATE_RETRIES = 2


def _provider_error_category(response: httpx.Response) -> str | None:
    """Read only a known error type; provider messages may contain secrets."""
    try:
        category = response.json().get("name")
    except (ValueError, AttributeError):
        return None
    allowed = {"rate_limit_exceeded", "daily_quota_exceeded", "monthly_quota_exceeded"}
    return category if isinstance(category, str) and category in allowed else None


def _retry_after_seconds(response: httpx.Response, fallback: float) -> float:
    value = response.headers.get("Retry-After", "").strip()
    try:
        seconds = float(value)
    except ValueError:
        try:
            timestamp = parsedate_to_datetime(value)
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)
            seconds = (timestamp - datetime.now(timezone.utc)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return fallback
    return max(0.0, seconds) if math.isfinite(seconds) else fallback


@lru_cache(maxsize=1)
def _logo_content() -> str:
    # Bundle a PNG with the API: email clients often do not render SVG, and
    # sending must not depend on the frontend's hostname or build directory.
    return b64encode(_LOGO_PATH.read_bytes()).decode("ascii")


def _email_layout(title: str, body: str) -> str:
    """Shared email-safe layout; callers must escape user-supplied HTML."""
    return (
        '<!doctype html><html lang="en"><body style="margin:0;padding:24px 12px;'
        'background-color:#f3f5f9;font-family:Arial,sans-serif;color:#172033;line-height:1.6">'
        '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">'
        '<tr><td align="center">'
        '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" '
        'style="max-width:560px;background-color:#ffffff;border-radius:12px">'
        '<tr><td style="padding:24px 28px;border-bottom:3px solid #f0b429">'
        f'<img src="cid:{_LOGO_CONTENT_ID}" alt="TRACE" width="198" height="60" '
        'style="display:block;width:198px;height:60px;border:0;background-color:#ffffff">'
        '</td></tr><tr><td style="padding:24px 28px">'
        f'<h1 style="margin:0 0 20px;font-size:24px;color:#16275e">{escape(title)}</h1>'
        f'{body}</td></tr><tr><td style="padding:18px 28px;border-top:1px solid #e4e8ef;'
        'font-size:12px;color:#5c6475">'
        f'TRACE &middot; {escape(settings.SYSTEM_LONG_NAME)}'
        '</td></tr></table></td></tr></table></body></html>'
    )


async def _send_email(email: str, *, subject: str, text: str, html: str) -> None:
    """Send a message, or allow an explicitly enabled local/debug path.

    With email enabled, a provider failure always fails delivery, even when
    temporary code logging is enabled. Success means the provider accepted the
    message, not that the recipient's mail server has already delivered it.
    """
    if not settings.EMAIL_ENABLED:
        if settings.DEBUG_OTP or settings.LOG_OTP_CODES:
            return
        log.error("Email is disabled and no development delivery is enabled.")
        raise EmailDeliveryError("Email delivery is not configured.")

    api_key = settings.RESEND_API_KEY.strip()
    sender = settings.EMAIL_FROM.strip()
    if not api_key or not sender:
        log.error("Email configuration requires RESEND_API_KEY and EMAIL_FROM.")
        raise EmailDeliveryError("Email delivery is not configured.")

    try:
        logo = _logo_content()
    except OSError:
        log.error("The bundled email logo could not be loaded.")
        raise EmailDeliveryError("Email delivery is not configured.") from None

    deadline = time.monotonic() + settings.EMAIL_QUEUE_TIMEOUT_SECONDS
    try:
        async with httpx.AsyncClient(timeout=settings.EMAIL_TIMEOUT_SECONDS) as client:
            for attempt in range(_MAX_RATE_RETRIES + 1):
                await _EMAIL_PACER.wait_turn(deadline)
                response = await client.post(
                    "https://api.resend.com/emails",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={
                        "from": sender,
                        "to": [email],
                        "subject": subject,
                        "text": text,
                        "html": html,
                        "attachments": [{
                            "filename": "trace-logo.png",
                            "content_type": "image/png",
                            "content_id": _LOGO_CONTENT_ID,
                            "content": logo,
                        }],
                    },
                )
                category = _provider_error_category(response) if response.status_code == 429 else None
                if category != "rate_limit_exceeded":
                    break
                # A named rate-limit response explicitly rejects this request.
                # Do not retry unknown failures/timeouts: they might have sent.
                _EMAIL_PACER.defer(_retry_after_seconds(response, fallback=2.0 ** attempt))
                if attempt >= _MAX_RATE_RETRIES:
                    break
                log.warning("Email provider request rate reached; retrying within the queue deadline.")
    except httpx.HTTPError as exc:
        # Provider exceptions and response bodies may contain email contents.
        # Keep both operator logs and public errors free of those contents.
        log.error("Email request failed (%s).", type(exc).__name__)
        raise EmailDeliveryError("Email delivery is temporarily unavailable.") from None

    if not 200 <= response.status_code < 300:
        log.error("Email provider rejected the request (HTTP %d, category %s).",
                  response.status_code, _provider_error_category(response) or "unknown")
        raise EmailDeliveryError("Email delivery is temporarily unavailable.")

    # A valid send acknowledgement has a message id. A malformed success must
    # not be presented as a delivered verification code.
    try:
        message_id = response.json().get("id")
    except (ValueError, AttributeError):
        message_id = None
    if not isinstance(message_id, str) or not message_id.strip():
        log.error("Email provider returned an invalid acknowledgement.")
        raise EmailDeliveryError("Email delivery is temporarily unavailable.")


async def send_otp_email(email: str, code: str, *, purpose: OTPPurpose) -> None:
    action = _PURPOSES[purpose]
    minutes = settings.OTP_EXPIRY_MINUTES
    text = (
        f"Your TRACE {action} code is: {code}\n\n"
        f"This code expires in {minutes} minutes and can be used once.\n"
        "Do not share it. If you did not request it, do not use the code.\n"
        "Contact your school administrator if you were not expecting this request."
    )
    body = (
        f"<p>Your {escape(action)} verification code is:</p>"
        '<p style="font-size:32px;font-weight:bold;letter-spacing:6px;color:#16275e">'
        f"{escape(code)}</p>"
        f"<p>This code expires in {minutes} minutes and can be used once.</p>"
        "<p>Do not share it. If you did not request it, do not use the code. "
        "Contact your school administrator if you were not expecting this request.</p>"
    )
    await _send_email(
        email, subject=f"TRACE: your {action} code", text=text,
        html=_email_layout(f"Your {action} code", body),
    )


async def send_account_decision_email(
    email: str, *, full_name: str, role: str, decision: AccountDecision,
    school_name: str | None = None, reason: str | None = None,
) -> None:
    """Notify a principal or teacher of their registration decision."""
    if decision not in ("approved", "rejected", "reopened", "blocked"):
        raise ValueError("Unsupported account decision.")
    role_label = "principal" if role == "admin" else "teacher"
    school = f" for {school_name}" if school_name else ""
    outcome = f"Your TRACE {role_label} account{school} has been {decision}."
    text = f"Hello {full_name},\n\n{outcome}\n\n"
    body = f"<p>Hello {escape(full_name)},</p><p>{escape(outcome)}</p>"
    if decision == "approved":
        next_step = (
            "You can now sign in to TRACE using the email address and password "
            "you registered with."
        )
        text += next_step
        body += f"<p>{next_step}</p>"
    elif decision in ("rejected", "blocked"):
        explanation = reason or "No reason was provided."
        contact = "system operator" if role == "admin" else "school administrator"
        next_step = f"Please contact your {contact} if you have questions about this decision."
        text += f"Reason: {explanation}\n\n{next_step}"
        body += (
            '<p style="padding:16px;background-color:#f3f5f9;white-space:pre-wrap">'
            f"<strong>Reason:</strong> {escape(explanation)}</p><p>{next_step}</p>"
        )
        if decision == "rejected":
            correction = (
                "To correct this application, open TRACE's sign-in page and select "
                "Correct and resubmit. Verify this email address to update your existing "
                "application; the corrected request will need approval again."
            )
            text += f"\n\n{correction}"
            body += f"<p>{escape(correction)}</p>"
    else:
        next_step = (
            "Your application has returned to Pending and is awaiting review. "
            "You will receive another email when a decision is made."
        )
        text += next_step
        body += f"<p>{next_step}</p>"
    text += f"\n\nTRACE - {settings.SYSTEM_LONG_NAME}"
    await _send_email(
        email, subject=f"TRACE: your account has been {decision}", text=text,
        html=_email_layout(f"Account {decision}", body),
    )


def log_otp_code(email: str, code: str, *, purpose: OTPPurpose) -> None:
    """Print committed OTPs only when temporary defense logging is explicit."""
    if settings.LOG_OTP_CODES:
        label = "login" if purpose == "login" else "password reset"
        print(f"[OTP] {label} code for {email}: {code}", flush=True)
