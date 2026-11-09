"""OTP delivery regressions using fake mail and a separate in-memory database.

Run from backend: python -m unittest discover -s tests -p test_otp_delivery.py
No test sends email or connects to the application's configured database.
"""
from __future__ import annotations

from contextlib import contextmanager, redirect_stdout
from datetime import datetime, timedelta, timezone
import io
import json
import logging
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from uuid import uuid4

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI
import bcrypt
import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models  # Register every model before creating the isolated schema.
from app.api import deps
from app.api.routers import auth
from app.core.config import settings
from app.db.session import Base, get_db
from app.models.schools import SCHOOL_APPROVED, School
from app.models.staff import (
    OTPCode, PasswordReset, ROLE_TEACHER, Staff, StaffDevice, StaffSession,
)
from app.services import email as email_service


ADDRESS = "teacher@example.com"
PASSWORD = "TestPassword1!"
FAKE_KEY = "resend-private-unit-test-key"
CODE = "483927"


@contextmanager
def captured_logs():
    """Capture application logs and legacy print output without leaking codes."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    root = logging.getLogger()
    previous_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    try:
        with redirect_stdout(stream):
            yield stream
    finally:
        root.removeHandler(handler)
        root.setLevel(previous_level)
        handler.close()


def provider_response(request):
    return httpx.Response(200, json={"id": "fake-email-id"})


class EmailServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.configuration = patch.multiple(
            settings,
            EMAIL_ENABLED=True,
            RESEND_API_KEY=FAKE_KEY,
            EMAIL_FROM="TRACE <noreply@example.com>",
            EMAIL_TIMEOUT_SECONDS=3.0,
            LOG_OTP_CODES=False,
            DEBUG_OTP=False,
        )
        self.configuration.start()
        self.addCleanup(self.configuration.stop)

    async def test_login_and_reset_send_the_supplied_code_to_the_recipient(self):
        for purpose in ("login", "password-reset"):
            with self.subTest(purpose=purpose):
                requests = []

                def handle(request):
                    requests.append(request)
                    return provider_response(request)

                client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
                with patch.object(email_service.httpx, "AsyncClient", return_value=client), captured_logs() as logs:
                    await email_service.send_otp_email(ADDRESS, CODE, purpose=purpose)

                self.assertEqual(len(requests), 1)
                request = requests[0]
                self.assertEqual(request.method, "POST")
                self.assertEqual(str(request.url), "https://api.resend.com/emails")
                self.assertEqual(request.headers["authorization"], f"Bearer {FAKE_KEY}")
                payload = json.loads(request.content)
                self.assertEqual(payload["from"], settings.EMAIL_FROM)
                self.assertIn(ADDRESS, payload["to"])
                self.assertIn(CODE, payload.get("text", "") + payload.get("html", ""))
                self.assertIn("sign-in" if purpose == "login" else "reset", payload["subject"].lower())
                self.assertNotIn(CODE, logs.getvalue())
                self.assertNotIn(FAKE_KEY, logs.getvalue())

    async def test_disabled_email_requires_an_explicit_local_or_defense_mode(self):
        settings.EMAIL_ENABLED = False
        with patch.object(email_service.httpx, "AsyncClient") as factory:
            with self.assertRaises(email_service.EmailDeliveryError):
                await email_service.send_otp_email(ADDRESS, CODE, purpose="login")
            for flag in ("DEBUG_OTP", "LOG_OTP_CODES"):
                with self.subTest(flag=flag), patch.object(settings, flag, True):
                    await email_service.send_otp_email(ADDRESS, CODE, purpose="login")
            factory.assert_not_called()

    async def test_enabled_email_with_missing_configuration_fails_before_network(self):
        for missing in ("RESEND_API_KEY", "EMAIL_FROM"):
            with self.subTest(missing=missing), patch.object(settings, missing, ""), patch.object(email_service.httpx, "AsyncClient") as factory:
                with self.assertRaises(email_service.EmailDeliveryError):
                    await email_service.send_otp_email(ADDRESS, CODE, purpose="login")
                factory.assert_not_called()

    async def test_provider_rejection_redacts_body_key_and_code(self):
        def reject(request):
            return httpx.Response(403, json={"message": f"sensitive {CODE} {FAKE_KEY}"})

        client = httpx.AsyncClient(transport=httpx.MockTransport(reject))
        with patch.object(email_service.httpx, "AsyncClient", return_value=client), captured_logs() as logs:
            with self.assertRaises(email_service.EmailDeliveryError) as raised:
                await email_service.send_otp_email(ADDRESS, CODE, purpose="login")
        output = logs.getvalue() + str(raised.exception)
        self.assertNotIn(CODE, output)
        self.assertNotIn(FAKE_KEY, output)
        self.assertNotIn("sensitive", output)

    async def test_timeout_redacts_transport_exception_and_uses_finite_timeout(self):
        def timeout(request):
            raise httpx.ReadTimeout(f"sensitive {CODE} {FAKE_KEY}", request=request)

        client = httpx.AsyncClient(transport=httpx.MockTransport(timeout))
        with patch.object(email_service.httpx, "AsyncClient", return_value=client) as factory, captured_logs() as logs:
            with self.assertRaises(email_service.EmailDeliveryError) as raised:
                await email_service.send_otp_email(ADDRESS, CODE, purpose="password-reset")
        self.assertEqual(factory.call_args.kwargs["timeout"], settings.EMAIL_TIMEOUT_SECONDS)
        output = logs.getvalue() + str(raised.exception)
        self.assertNotIn(CODE, output)
        self.assertNotIn(FAKE_KEY, output)
        self.assertNotIn("sensitive", output)

    async def test_success_without_a_valid_provider_acknowledgement_is_rejected(self):
        replies = (
            httpx.Response(200, json={}),
            httpx.Response(200, json={"id": " "}),
            httpx.Response(200, json=[]),
            httpx.Response(200, text=f"sensitive {CODE} {FAKE_KEY}"),
        )
        for reply in replies:
            with self.subTest(reply=reply.content):
                client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: reply))
                with patch.object(email_service.httpx, "AsyncClient", return_value=client), captured_logs() as logs:
                    with self.assertRaises(email_service.EmailDeliveryError) as raised:
                        await email_service.send_otp_email(ADDRESS, CODE, purpose="login")
                output = logs.getvalue() + str(raised.exception)
                self.assertNotIn(CODE, output)
                self.assertNotIn(FAKE_KEY, output)
                self.assertNotIn("sensitive", output)

    def test_code_logging_is_explicit_and_independent_of_response_debugging(self):
        for purpose in ("login", "password-reset"):
            with self.subTest(purpose=purpose), captured_logs() as logs:
                email_service.log_otp_code(ADDRESS, CODE, purpose=purpose)
                self.assertNotIn(CODE, logs.getvalue())
            with self.subTest(purpose=purpose), patch.object(settings, "LOG_OTP_CODES", True), captured_logs() as logs:
                email_service.log_otp_code(ADDRESS, CODE, purpose=purpose)
                self.assertIn(CODE, logs.getvalue())
                self.assertIn(ADDRESS, logs.getvalue())
                self.assertIn("login" if purpose == "login" else "reset", logs.getvalue().lower())
                self.assertNotIn(FAKE_KEY, logs.getvalue())


class OTPRouteTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        # Exercise real password verification without production hash cost.
        cls.password_hash = bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt(rounds=4)).decode()

    async def asyncSetUp(self):
        self.configuration = patch.multiple(
            settings,
            EMAIL_ENABLED=True,
            RESEND_API_KEY=FAKE_KEY,
            EMAIL_FROM="TRACE <noreply@example.com>",
            EMAIL_TIMEOUT_SECONDS=3.0,
            LOG_OTP_CODES=True,
            DEBUG_OTP=False,
            SECRET_KEY="unit-test-jwt-secret-not-a-production-key",
        )
        self.configuration.start()
        self.addCleanup(self.configuration.stop)
        deps._BUCKETS.clear()
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False, autoflush=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.staff_id = uuid4()
        async with self.sessions() as db:
            db.add(School(school_id=1, name="Synthetic Test School", status=SCHOOL_APPROVED))
            await db.flush()
            db.add(Staff(
                id=self.staff_id, school_id=1, role=ROLE_TEACHER,
                email=ADDRESS, password_hash=self.password_hash,
                first_name="Test", last_name="Teacher", contact_number="",
                is_active=True, is_approved=True,
            ))
            await db.commit()

        async def isolated_db():
            async with self.sessions() as db:
                try:
                    yield db
                except Exception:
                    await db.rollback()
                    raise

        application = FastAPI()
        application.include_router(auth.router)
        application.dependency_overrides[get_db] = isolated_db
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application, client=("127.0.0.1", 12345)),
            base_url="http://testserver",
        )

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.engine.dispose()
        deps._BUCKETS.clear()

    async def _issue(self, purpose):
        if purpose == "login":
            return await self.client.post("/api/auth/login", json={
                "email": ADDRESS, "password": PASSWORD, "device_id": "new-test-device",
            })
        return await self.client.post("/api/auth/forgot-password", json={"email": ADDRESS})

    async def _count(self, model):
        async with self.sessions() as db:
            return (await db.execute(select(func.count()).select_from(model))).scalar_one()

    async def test_login_emails_and_logs_the_same_valid_code_without_ui_exposure(self):
        sent = []

        def handle(request):
            sent.append(json.loads(request.content))
            return provider_response(request)

        provider = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        with patch.object(email_service.httpx, "AsyncClient", return_value=provider), captured_logs() as logs:
            response = await self._issue("login")
        self.assertEqual(response.status_code, 201, response.text)
        self.assertTrue(response.json()["requires_2fa"])
        self.assertFalse(response.json().get("access_token"))
        self.assertFalse(response.json().get("refresh_token"))
        self.assertFalse(response.json().get("debug_otp"))
        self.assertEqual(len(sent), 1)
        async with self.sessions() as db:
            otp = (await db.execute(select(OTPCode))).scalar_one()
            self.assertEqual(otp.purpose, "login")
            self.assertEqual(len(otp.code), 6)
            self.assertFalse(otp.is_used)
            self.assertGreater(auth._aware(otp.expires_at), datetime.now(timezone.utc))
            self.assertLessEqual(
                auth._aware(otp.expires_at),
                datetime.now(timezone.utc) + timedelta(minutes=settings.OTP_EXPIRY_MINUTES),
            )
            self.assertIn(otp.code, json.dumps(sent[0]))
            self.assertIn(otp.code, logs.getvalue())
            self.assertNotIn(otp.code, response.text)
        verified = await self.client.post("/api/auth/verify-otp", json={
            "user_id": str(self.staff_id), "code": otp.code, "device_id": "new-test-device",
        })
        self.assertEqual(verified.status_code, 201, verified.text)
        self.assertTrue(verified.json()["access_token"])

    async def test_reset_emails_and_logs_the_same_valid_code_without_ui_exposure(self):
        sent = []

        def handle(request):
            sent.append(json.loads(request.content))
            return provider_response(request)

        provider = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        with patch.object(email_service.httpx, "AsyncClient", return_value=provider), captured_logs() as logs:
            response = await self._issue("password-reset")
        self.assertEqual(response.status_code, 202, response.text)
        self.assertNotIn("debug_otp", response.json())
        self.assertEqual(len(sent), 1)
        async with self.sessions() as db:
            otp = (await db.execute(select(OTPCode))).scalar_one()
            self.assertEqual(otp.purpose, "password-reset")
            self.assertIn(otp.code, json.dumps(sent[0]))
            self.assertIn(otp.code, logs.getvalue())
            self.assertNotIn(otp.code, response.text)
        verified = await self.client.post("/api/auth/verify-reset-otp", json={"email": ADDRESS, "code": otp.code})
        self.assertEqual(verified.status_code, 200, verified.text)
        self.assertTrue(verified.json()["token"])

    async def test_email_still_sends_with_defense_logging_disabled(self):
        settings.LOG_OTP_CODES = False
        for purpose in ("login", "password-reset"):
            with self.subTest(purpose=purpose):
                provider = httpx.AsyncClient(transport=httpx.MockTransport(provider_response))
                with patch.object(auth, "generate_otp", return_value=CODE), patch.object(email_service.httpx, "AsyncClient", return_value=provider) as factory, captured_logs() as logs:
                    response = await self._issue(purpose)
                self.assertEqual(response.status_code, 201 if purpose == "login" else 202, response.text)
                factory.assert_called_once()
                self.assertNotIn(CODE, logs.getvalue())
                self.assertNotIn(CODE, response.text)

    async def test_login_delivery_failure_rolls_back_code_and_issues_no_session(self):
        def reject(request):
            return httpx.Response(403, text=f"sensitive {CODE} {FAKE_KEY}")

        provider = httpx.AsyncClient(transport=httpx.MockTransport(reject))
        with patch.object(auth, "generate_otp", return_value=CODE), patch.object(email_service.httpx, "AsyncClient", return_value=provider), captured_logs() as logs:
            response = await self._issue("login")
        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(await self._count(OTPCode), 0)
        self.assertEqual(await self._count(StaffSession), 0)
        output = response.text + logs.getvalue()
        self.assertNotIn(CODE, output)
        self.assertNotIn(FAKE_KEY, output)
        self.assertNotIn("sensitive", output)

    async def test_reset_delivery_failure_remains_generic_and_rolls_back_issuance(self):
        def reject(request):
            return httpx.Response(403, text=f"sensitive {CODE} {FAKE_KEY}")

        provider = httpx.AsyncClient(transport=httpx.MockTransport(reject))
        with patch.object(auth, "generate_otp", return_value=CODE), patch.object(email_service.httpx, "AsyncClient", return_value=provider), captured_logs() as logs:
            response = await self._issue("password-reset")
        unknown = await self.client.post("/api/auth/forgot-password", json={"email": "unknown@example.com"})
        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(response.json(), unknown.json())
        self.assertEqual(await self._count(OTPCode), 0)
        self.assertEqual(await self._count(PasswordReset), 0)
        output = response.text + logs.getvalue()
        self.assertNotIn(CODE, output)
        self.assertNotIn(FAKE_KEY, output)
        self.assertNotIn("sensitive", output)

    async def test_a_code_is_not_logged_when_database_commit_fails(self):
        for purpose in ("login", "password-reset"):
            with self.subTest(purpose=purpose):
                provider = httpx.AsyncClient(transport=httpx.MockTransport(provider_response))
                with patch.object(auth, "generate_otp", return_value=CODE), patch.object(email_service.httpx, "AsyncClient", return_value=provider), patch.object(AsyncSession, "commit", side_effect=RuntimeError("synthetic commit failure")), captured_logs() as logs:
                    with self.assertRaisesRegex(RuntimeError, "synthetic commit failure"):
                        await self._issue(purpose)
                self.assertNotIn(CODE, logs.getvalue())
                self.assertEqual(await self._count(OTPCode), 0)

    async def test_unknown_reset_address_never_sends_or_logs_a_code(self):
        with patch.object(email_service.httpx, "AsyncClient") as factory, captured_logs() as logs:
            response = await self.client.post("/api/auth/forgot-password", json={"email": "unknown@example.com"})
        self.assertEqual(response.status_code, 202)
        factory.assert_not_called()
        self.assertEqual(await self._count(OTPCode), 0)
        self.assertNotIn("debug_otp", response.json())
        self.assertNotIn("unknown@example.com", logs.getvalue())

    async def test_trusted_device_login_does_not_send_or_log_an_otp(self):
        async with self.sessions() as db:
            db.add(StaffDevice(
                staff_id=self.staff_id, device_id="new-test-device", is_trusted=True,
                last_2fa_verified=datetime.now(timezone.utc) - timedelta(days=1),
            ))
            await db.commit()
        with patch.object(email_service.httpx, "AsyncClient") as factory, patch.object(auth, "generate_otp") as generate:
            response = await self._issue("login")
        self.assertEqual(response.status_code, 201, response.text)
        self.assertTrue(response.json()["access_token"])
        factory.assert_not_called()
        generate.assert_not_called()
        self.assertEqual(await self._count(OTPCode), 0)


if __name__ == "__main__":
    unittest.main()
