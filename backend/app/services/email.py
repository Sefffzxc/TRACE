"""Deliver OTPs through Resend, with a separate temporary defense log switch.

The HTTP API uses the existing httpx dependency. Configuration belongs only on
the API server; neither the email credential nor OTPs belong in frontend builds.
"""
from __future__ import annotations

from html import escape
import logging
from typing import Literal

import httpx

from app.core.config import settings


log = logging.getLogger("trace.email")
OTPPurpose = Literal["login", "password-reset"]
_PURPOSES = {"login": "sign-in", "password-reset": "password reset"}


class EmailDeliveryError(Exception):
    """Delivery failed; messages must never contain credentials or email bodies."""


async def send_otp_email(email: str, code: str, *, purpose: OTPPurpose) -> None:
    """Send one code, or allow an explicitly enabled local/debug delivery path.

    With email enabled, a provider failure always fails delivery, even when
    temporary code logging is enabled. Success means the provider accepted the
    message, not that the recipient's mail server has already delivered it.
    """
    if not settings.EMAIL_ENABLED:
        if settings.DEBUG_OTP or settings.LOG_OTP_CODES:
            return
        log.error("OTP email is disabled and no development delivery is enabled.")
        raise EmailDeliveryError("Verification email is not configured.")

    api_key = settings.RESEND_API_KEY.strip()
    sender = settings.EMAIL_FROM.strip()
    if not api_key or not sender:
        log.error("OTP email configuration requires RESEND_API_KEY and EMAIL_FROM.")
        raise EmailDeliveryError("Verification email is not configured.")

    action = _PURPOSES[purpose]
    minutes = settings.OTP_EXPIRY_MINUTES
    subject = f"TRACE: your {action} code"
    text = (
        f"Your TRACE {action} code is: {code}\n\n"
        f"This code expires in {minutes} minutes and can be used once.\n"
        "Do not share it. If you did not request it, do not use the code.\n"
        "Contact your school administrator if you were not expecting this request."
    )
    html = (
        '<div style="font-family:Arial,sans-serif;max-width:480px;margin:auto;'
        'color:#172033;line-height:1.6">'
        '<h1 style="font-size:24px">TRACE</h1>'
        f"<p>Your {escape(action)} verification code is:</p>"
        '<p style="font-size:32px;font-weight:bold;letter-spacing:6px">'
        f"{escape(code)}</p>"
        f"<p>This code expires in {minutes} minutes and can be used once.</p>"
        "<p>Do not share it. If you did not request it, do not use the code. "
        "Contact your school administrator if you were not expecting this request.</p>"
        "</div>"
    )

    try:
        async with httpx.AsyncClient(timeout=settings.EMAIL_TIMEOUT_SECONDS) as client:
            response = await client.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "from": sender,
                    "to": [email],
                    "subject": subject,
                    "text": text,
                    "html": html,
                },
            )
    except httpx.HTTPError as exc:
        # Provider exceptions and response bodies may contain email contents.
        # Keep both operator logs and public errors free of those contents.
        log.error("OTP email request failed (%s).", type(exc).__name__)
        raise EmailDeliveryError("Verification email is temporarily unavailable.") from None

    if not 200 <= response.status_code < 300:
        log.error("OTP email provider rejected the request (HTTP %d).", response.status_code)
        raise EmailDeliveryError("Verification email is temporarily unavailable.")

    # A valid send acknowledgement has a message id. A malformed success must
    # not be presented as a delivered verification code.
    try:
        message_id = response.json().get("id")
    except (ValueError, AttributeError):
        message_id = None
    if not isinstance(message_id, str) or not message_id.strip():
        log.error("OTP email provider returned an invalid acknowledgement.")
        raise EmailDeliveryError("Verification email is temporarily unavailable.")


def log_otp_code(email: str, code: str, *, purpose: OTPPurpose) -> None:
    """Print committed OTPs only when temporary defense logging is explicit."""
    if settings.LOG_OTP_CODES:
        label = "login" if purpose == "login" else "password reset"
        print(f"[OTP] {label} code for {email}: {code}", flush=True)
