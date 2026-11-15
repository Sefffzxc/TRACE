"""CAPTCHA registration/login regressions with isolated SQLite and fake HTTP.

Run from backend: python -m unittest discover -s tests -p test_captcha_auth.py
No test connects to a real database or to Cloudflare, or sends a real email.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs
from uuid import uuid4

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bcrypt
from fastapi import FastAPI
import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models
from app.api import deps
from app.api.routers import auth, schools
from app.core.config import settings
from app.db.session import Base, get_db
from app.models.schools import School
from app.models.staff import AuditLog, OTPCode, Staff, StaffSession
from app.services import captcha
from app.services.login_captcha import login_risk


HTTPClient = httpx.AsyncClient
PASSWORD = "TestPassword1!"
EMAIL = "approved@example.com"
SECRET = "private-unit-test-turnstile-secret"
SITE_KEY = "public-unit-test-turnstile-site-key"
CLIENT_IP = "198.51.100.15"
TOKEN_ACTIONS = {
    "valid-teacher-token": "register_teacher",
    "valid-school-token": "register_school",
    "valid-login-token": "login",
}


class CaptchaAuthenticationTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.password_hash = bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt(rounds=4)).decode()

    async def asyncSetUp(self):
        configuration = patch.multiple(
            settings,
            CAPTCHA_ENABLED=True, TURNSTILE_SITE_KEY=SITE_KEY,
            TURNSTILE_SECRET_KEY=SECRET, TURNSTILE_ALLOWED_HOSTNAMES="traceedu.app,www.traceedu.app",
            CAPTCHA_LOGIN_THRESHOLD=3, CAPTCHA_TIMEOUT_SECONDS=3.0,
            MAX_LOGIN_ATTEMPTS=5, MAX_LOCKOUTS=3,
            DEBUG_OTP=False, LOG_OTP_CODES=False,
        )
        configuration.start()
        self.addCleanup(configuration.stop)
        login_risk.clear()
        deps._BUCKETS.clear()

        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False, autoflush=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.staff_id = uuid4()
        async with self.sessions() as db:
            db.add(School(school_id=1, name="Existing Test School", city="Cagayan de Oro", status="approved"))
            await db.flush()
            db.add(Staff(
                id=self.staff_id, school_id=1, role="teacher", email=EMAIL,
                password_hash=self.password_hash, first_name="Approved", last_name="Teacher",
                contact_number="09123456789", account_status="approved", is_approved=True,
                is_active=True,
            ))
            await db.commit()

        async def isolated_db():
            async with self.sessions() as db:
                try:
                    yield db
                except Exception:
                    await db.rollback()
                    raise

        async def no_throttle():
            # Separate rate limiting has its own coverage; do not let a test's
            # repeated submissions hide CAPTCHA behavior behind an HTTP 429.
            return None

        self.application = FastAPI()
        self.application.include_router(auth.router)
        self.application.include_router(schools.router)
        self.application.dependency_overrides[get_db] = isolated_db
        self.application.dependency_overrides[deps.limit_login] = no_throttle
        self.application.dependency_overrides[deps.limit_register] = no_throttle
        self.client = self._client(CLIENT_IP)

        self.provider_requests = []
        self.provider_failure = None

        def siteverify(request):
            self.provider_requests.append(request)
            if self.provider_failure is not None:
                return httpx.Response(200, json={
                    "success": False, "error-codes": [self.provider_failure],
                })
            body = parse_qs(request.content.decode())
            token = body.get("response", [""])[0]
            action = TOKEN_ACTIONS.get(token)
            if action is None:
                return httpx.Response(200, json={
                    "success": False, "error-codes": ["invalid-input-response"],
                })
            return httpx.Response(200, json={
                "success": True, "hostname": "traceedu.app", "action": action,
            })

        factory = patch.object(
            captcha.httpx, "AsyncClient",
            side_effect=lambda **kwargs: HTTPClient(transport=httpx.MockTransport(siteverify)),
        )
        factory.start()
        self.addCleanup(factory.stop)
        send = patch.object(auth, "send_otp_email", new_callable=AsyncMock)
        self.send_email = send.start()
        self.addCleanup(send.stop)

    def _client(self, ip):
        return HTTPClient(
            transport=httpx.ASGITransport(app=self.application, client=(ip, 45000)),
            base_url="http://testserver",
        )

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.engine.dispose()
        login_risk.clear()
        deps._BUCKETS.clear()

    def teacher_payload(self, token=None):
        payload = {
            "school_id": 1, "email": "newteacher@example.com", "password": PASSWORD,
            "first_name": "New", "last_name": "Teacher", "contact_number": "09123456789",
        }
        if token is not None:
            payload["captcha_token"] = token
        return payload

    def school_payload(self, token=None):
        payload = {
            "school": {"name": "New Test School", "city": "Iligan", "school_type": "public"},
            "email": "newprincipal@example.com", "password": PASSWORD,
            "first_name": "New", "last_name": "Principal", "contact_number": "09123456789",
        }
        if token is not None:
            payload["captcha_token"] = token
        return payload

    async def login(self, *, password=PASSWORD, email=EMAIL, token=None, client=None, headers=None):
        payload = {"email": email, "password": password, "device_id": "unit-test-new-device"}
        if token is not None:
            payload["captcha_token"] = token
        return await (client or self.client).post("/api/auth/login", json=payload, headers=headers)

    async def count(self, model):
        async with self.sessions() as db:
            return (await db.execute(select(func.count()).select_from(model))).scalar_one()

    async def staff(self):
        async with self.sessions() as db:
            return await db.get(Staff, self.staff_id)

    async def set_staff(self, **values):
        async with self.sessions() as db:
            person = await db.get(Staff, self.staff_id)
            for key, value in values.items():
                setattr(person, key, value)
            await db.commit()

    def assert_captcha_error(self, response, code="captcha_required", status=400):
        self.assertEqual(response.status_code, status, response.text)
        detail = response.json()["detail"]
        self.assertEqual(detail["code"], code)
        self.assertIs(detail["captcha_required"], True)
        self.assertNotIn(SECRET, response.text)
        self.assertNotIn("access_token", response.json())

    async def assert_no_authentication_created(self):
        self.assertEqual(await self.count(OTPCode), 0)
        self.assertEqual(await self.count(StaffSession), 0)
        self.send_email.assert_not_awaited()

    async def test_public_configuration_contains_only_public_key_and_enabled_flag(self):
        response = await self.client.get("/api/auth/captcha-config")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"enabled": True, "site_key": SITE_KEY})
        self.assertNotIn(SECRET, response.text)
        self.assertNotIn("secret", response.text.lower())
        settings.CAPTCHA_ENABLED = False
        response = await self.client.get("/api/auth/captcha-config")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIs(response.json()["enabled"], False)
        self.assertFalse(response.json()["site_key"])

    async def test_both_registrations_require_captcha_before_database_reads_or_password_hashing(self):
        with patch.object(AsyncSession, "execute", side_effect=AssertionError("Unexpected database query")):
            with patch.object(auth, "get_password_hash") as teacher_hash:
                with patch.object(schools, "get_password_hash") as principal_hash:
                    for path, payload in (
                        ("/api/auth/register", self.teacher_payload()),
                        ("/api/schools/register", self.school_payload()),
                    ):
                        with self.subTest(path=path):
                            response = await self.client.post(path, json=payload)
                            self.assert_captcha_error(response)
                    teacher_hash.assert_not_called()
                    principal_hash.assert_not_called()
        self.assertEqual(len(self.provider_requests), 0)
        self.assertEqual(await self.count(Staff), 1)
        self.assertEqual(await self.count(School), 1)
        self.assertEqual(await self.count(AuditLog), 0)

    async def test_expired_or_wrong_form_tokens_leave_both_registrations_unchanged(self):
        for failure in ("timeout-or-duplicate", None):
            self.provider_failure = failure
            for path, payload in (
                ("/api/auth/register", self.teacher_payload("valid-login-token")),
                ("/api/schools/register", self.school_payload("valid-login-token")),
            ):
                with self.subTest(path=path, provider_failure=failure):
                    response = await self.client.post(path, json=payload)
                    self.assert_captcha_error(response, "captcha_invalid")
        self.assertEqual(await self.count(Staff), 1)
        self.assertEqual(await self.count(School), 1)
        self.assertEqual(await self.count(AuditLog), 0)

    async def test_valid_teacher_and_principal_requests_are_saved_pending(self):
        response = await self.client.post(
            "/api/auth/register", json=self.teacher_payload("valid-teacher-token"),
        )
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["account_status"], "pending")
        self.assertIs(response.json()["is_approved"], False)
        response = await self.client.post(
            "/api/schools/register", json=self.school_payload("valid-school-token"),
        )
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["status"], "pending")
        async with self.sessions() as db:
            teacher = (await db.execute(select(Staff).where(Staff.email == "newteacher@example.com"))).scalar_one()
            principal = (await db.execute(select(Staff).where(Staff.email == "newprincipal@example.com"))).scalar_one()
            school = await db.get(School, principal.school_id)
            self.assertEqual(teacher.role, "teacher")
            self.assertEqual(principal.role, "admin")
            self.assertEqual(teacher.account_status, "pending")
            self.assertEqual(principal.account_status, "pending")
            self.assertFalse(teacher.is_approved)
            self.assertFalse(principal.is_approved)
            self.assertEqual(school.status, "pending")
        self.assertEqual(len(self.provider_requests), 2)
        await self.assert_no_authentication_created()

    async def test_normal_first_login_still_uses_email_otp_without_captcha(self):
        response = await self.login()
        self.assertEqual(response.status_code, 201, response.text)
        self.assertIs(response.json()["requires_2fa"], True)
        self.assertIsNone(response.json()["access_token"])
        self.assertEqual(len(self.provider_requests), 0)
        self.send_email.assert_awaited_once()
        self.assertEqual(await self.count(OTPCode), 1)
        self.assertEqual(await self.count(StaffSession), 0)

    async def test_third_wrong_password_requests_captcha_and_fourth_is_blocked_before_hash(self):
        for attempt in range(1, 4):
            response = await self.login(password="WrongPassword1!")
            self.assertEqual(response.status_code, 401, response.text)
            if attempt == 3:
                self.assertEqual(response.json()["detail"]["code"], "invalid_credentials")
                self.assertIs(response.json()["detail"]["captcha_required"], True)
        self.assertEqual((await self.staff()).login_attempts, 3)
        with patch.object(auth, "verify_password") as verify_password:
            response = await self.login()
            self.assert_captcha_error(response)
            verify_password.assert_not_called()
        self.assertEqual((await self.staff()).login_attempts, 3)
        self.assertEqual(len(self.provider_requests), 0)
        await self.assert_no_authentication_created()

    async def test_failed_captcha_does_not_consume_password_attempts_or_issue_otp(self):
        await self.set_staff(login_attempts=3)
        self.provider_failure = "timeout-or-duplicate"
        with patch.object(auth, "verify_password") as verify_password:
            response = await self.login(token="valid-login-token")
            self.assert_captcha_error(response, "captcha_invalid")
            verify_password.assert_not_called()
        self.assertEqual((await self.staff()).login_attempts, 3)
        await self.assert_no_authentication_created()

    async def test_valid_captcha_preserves_wrong_password_count_and_normal_lockout(self):
        await self.set_staff(login_attempts=3)
        response = await self.login(password="WrongPassword1!", token="valid-login-token")
        self.assertEqual(response.status_code, 401, response.text)
        self.assertEqual((await self.staff()).login_attempts, 4)
        response = await self.login(password="WrongPassword1!", token="valid-login-token")
        self.assertEqual(response.status_code, 401, response.text)
        self.assertIn("locked", response.text.lower())
        person = await self.staff()
        self.assertEqual(person.login_attempts, 0)
        self.assertEqual(person.lockout_count, 1)
        self.assertIsNotNone(person.locked_until)
        await self.assert_no_authentication_created()

    async def test_valid_captcha_and_password_clear_account_attempts_then_require_otp(self):
        await self.set_staff(login_attempts=3)
        response = await self.login(token="valid-login-token")
        self.assertEqual(response.status_code, 201, response.text)
        self.assertIs(response.json()["requires_2fa"], True)
        self.assertEqual((await self.staff()).login_attempts, 0)
        self.assertEqual((await self.staff()).lockout_count, 0)
        self.send_email.assert_awaited_once()
        self.assertEqual(await self.count(OTPCode), 1)
        self.assertEqual(await self.count(StaffSession), 0)

    async def test_changing_ip_does_not_erase_persisted_account_risk(self):
        for _ in range(3):
            await self.login(password="WrongPassword1!")
        login_risk.clear()  # Also models a process restart losing only IP counters.
        async with self._client("198.51.100.99") as other_client:
            response = await self.login(client=other_client)
        self.assert_captcha_error(response)
        self.assertEqual((await self.staff()).login_attempts, 3)
        await self.assert_no_authentication_created()

    async def test_an_expired_prior_lockout_still_requires_captcha(self):
        await self.set_staff(
            login_attempts=0, lockout_count=1,
            locked_until=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
        response = await self.login()
        self.assert_captcha_error(response)
        self.assertEqual((await self.staff()).lockout_count, 1)
        await self.assert_no_authentication_created()

    async def test_valid_captcha_cannot_bypass_temporary_or_permanent_account_lock(self):
        for permanent, expected_status in ((False, 401), (True, 403)):
            with self.subTest(permanent=permanent):
                await self.set_staff(
                    login_attempts=0, lockout_count=1, permanent_lock=permanent,
                    locked_until=datetime.now(timezone.utc) + timedelta(minutes=5),
                )
                with patch.object(auth, "verify_password") as verify_password:
                    response = await self.login(token="valid-login-token")
                    self.assertEqual(response.status_code, expected_status, response.text)
                    self.assertIn("locked", response.text.lower())
                    verify_password.assert_not_called()
        await self.assert_no_authentication_created()

    async def test_unknown_email_spray_triggers_captcha_for_same_ip_and_ignores_spoofed_headers(self):
        for attempt in range(3):
            response = await self.login(
                email=f"unknown{attempt}@example.com", password="WrongPassword1!",
                headers={"X-Forwarded-For": f"203.0.113.{attempt + 1}"},
            )
            self.assertEqual(response.status_code, 401, response.text)
            self.assertNotIn("does not exist", response.text)
        self.assertIs(response.json()["detail"]["captcha_required"], True)
        response = await self.login(headers={"X-Forwarded-For": "203.0.113.250"})
        self.assert_captcha_error(response)
        response = await self.login(email="anotherunknown@example.com")
        self.assert_captcha_error(response)
        self.assertEqual((await self.staff()).login_attempts, 0)
        await self.assert_no_authentication_created()

    async def test_captcha_provider_outage_cannot_create_accounts_or_authenticate(self):
        self.provider_failure = "internal-error"
        response = await self.client.post(
            "/api/auth/register", json=self.teacher_payload("valid-teacher-token"),
        )
        self.assert_captcha_error(response, "captcha_unavailable", status=503)
        await self.set_staff(login_attempts=3)
        response = await self.login(token="valid-login-token")
        self.assert_captcha_error(response, "captcha_unavailable", status=503)
        self.assertEqual(await self.count(Staff), 1)
        self.assertEqual((await self.staff()).login_attempts, 3)
        await self.assert_no_authentication_created()

    async def test_disabled_captcha_preserves_existing_registration_and_login_behavior(self):
        settings.CAPTCHA_ENABLED = False
        response = await self.client.post("/api/auth/register", json=self.teacher_payload())
        self.assertEqual(response.status_code, 201, response.text)
        response = await self.client.post("/api/schools/register", json=self.school_payload())
        self.assertEqual(response.status_code, 201, response.text)
        await self.set_staff(login_attempts=3)
        response = await self.login(password="WrongPassword1!")
        self.assertEqual(response.status_code, 401, response.text)
        self.assertIsInstance(response.json()["detail"], str)
        self.assertNotIn("captcha", response.text)
        self.assertEqual((await self.staff()).login_attempts, 4)
        self.assertEqual(len(self.provider_requests), 0)


if __name__ == "__main__":
    unittest.main()
