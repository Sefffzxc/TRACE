"""Old authentication work cannot restore credentials after an email correction.

All accounts/messages are synthetic. Optional PostgreSQL cases require an
explicit loopback TRACE_TEST_POSTGRES_URL and use their own temporary schema.
"""
from __future__ import annotations

import asyncio
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI, Request
import httpx
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateSchema, DropSchema

import app.models
from app.api import deps
from app.api.routers import auth, staff as staff_routes
from app.core import security
from app.core.config import settings
from app.db.session import Base, get_db
from app.models.email_corrections import EmailCorrectionChallenge
from app.models.schools import School
from app.models.staff import OTPCode, PasswordReset, Staff, StaffDevice, StaffSession
from app.schemas.email_corrections import EmailCorrectionVerify
from app.services import email_corrections


_SESSION = ContextVar("email_correction_auth_test_session", default=None)
TEST_POSTGRES_URL = os.environ.get("TRACE_TEST_POSTGRES_URL")


class EmailCorrectionAuthTests(unittest.IsolatedAsyncioTestCase):
    postgres_url = None

    async def asyncSetUp(self):
        config = patch.multiple(settings, SECRET_KEY="isolated-auth-correction-secret",
                                CAPTCHA_ENABLED=False, DEBUG_OTP=False, LOG_OTP_CODES=False)
        config.start()
        self.addCleanup(config.stop)
        deps._BUCKETS.clear()
        self.schema = None
        if self.postgres_url:
            if make_url(self.postgres_url).host not in {"127.0.0.1", "localhost", "::1"}:
                self.fail("Auth tests require a disposable loopback PostgreSQL database.")
            self.schema = "trace_auth_correction_test_" + uuid4().hex
            self.engine = create_async_engine(self.postgres_url, connect_args={
                "server_settings": {"search_path": self.schema},
            })
        else:
            self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False, autoflush=False)
        async with self.engine.begin() as connection:
            if self.schema:
                await connection.execute(CreateSchema(self.schema))
            else:
                await connection.execute(text("PRAGMA foreign_keys=ON"))
            await connection.run_sync(Base.metadata.create_all)
        self.teacher_id, self.admin_id = uuid4(), uuid4()
        async with self.sessions() as db:
            db.add(School(school_id=1, name="Synthetic School", status="approved"))
            await db.flush()
            for staff_id, role, email in ((self.teacher_id, "teacher", "teacher@example.com"),
                                          (self.admin_id, "admin", "admin@example.com")):
                db.add(Staff(id=staff_id, school_id=1, role=role, email=email,
                             password_hash="old-hash", first_name="Synthetic", last_name=role,
                             contact_number="09123456789", account_status="approved",
                             is_active=True, is_approved=True))
            await db.flush()
            device = StaffDevice(staff_id=self.teacher_id, device_id="known-device", is_trusted=True,
                                 last_2fa_verified=auth._now())
            db.add(device)
            await db.flush()
            self.device_id = device.id
            self.old_access = security.create_access_token({"sub": str(self.teacher_id), "role": "teacher"})
            self.old_refresh = security.create_refresh_token({"sub": str(self.teacher_id)})
            db.add(StaffSession(staff_id=self.teacher_id, device_id=device.id,
                                access_token=self.old_access, refresh_token=self.old_refresh,
                                expires_at=auth._now() + timedelta(minutes=15)))
            await db.commit()

        async def isolated_db():
            async with self.sessions() as db:
                token = _SESSION.set(db)
                try:
                    yield db
                except Exception:
                    await db.rollback()
                    raise
                finally:
                    _SESSION.reset(token)

        application = FastAPI()
        application.include_router(auth.router)
        application.include_router(staff_routes.router)
        application.dependency_overrides[get_db] = isolated_db
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://testserver")

        async def verify_password(password, password_hash):
            self.assertFalse(_SESSION.get().in_transaction())
            return password == "OldPassword1!" and password_hash == "old-hash"

        async def hash_password(password):
            self.assertFalse(_SESSION.get().in_transaction())
            return "new-hash"

        async def send_code(*args, **kwargs):
            self.assertFalse(_SESSION.get().in_transaction())

        for module, name, value in (
            (auth, "async_verify_password", AsyncMock(side_effect=verify_password)),
            (auth, "async_get_password_hash", AsyncMock(side_effect=hash_password)),
            (auth, "send_otp_email", AsyncMock(side_effect=send_code)),
            (staff_routes, "async_verify_password", AsyncMock(side_effect=verify_password)),
            (staff_routes, "async_get_password_hash", AsyncMock(side_effect=hash_password)),
            (email_corrections, "send_email_correction_notice", AsyncMock()),
        ):
            patcher = patch.object(module, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    async def asyncTearDown(self):
        await self.client.aclose()
        if self.schema:
            async with self.engine.begin() as connection:
                await connection.execute(DropSchema(self.schema, cascade=True))
        await self.engine.dispose()
        deps._BUCKETS.clear()

    async def _complete_correction(self):
        challenge_id, code = uuid4(), "147258"
        async with self.sessions() as db:
            actor, target, schools = await email_corrections._lock_context(db, self.admin_id, self.teacher_id)
            db.add(EmailCorrectionChallenge(
                id=challenge_id, actor_id=actor.id, staff_id=target.id,
                new_email="corrected@example.com", reason="Identity confirmed in person.",
                code_digest=email_corrections.code_digest(challenge_id, code),
                state_digest=email_corrections._state_digest(actor, target, schools),
                expires_at=auth._now() + timedelta(minutes=5), delivered_at=auth._now(),
            ))
            await db.commit()
        async with self.sessions() as db:
            request = Request({"type": "http", "headers": [], "client": ("127.0.0.1", 12345)})
            return await email_corrections.verify_email_correction(db, EmailCorrectionVerify(
                challenge_id=challenge_id, new_email="corrected@example.com", code=code,
            ), request)

    async def _login(self, *, email="teacher@example.com", device="known-device", password="OldPassword1!"):
        return await self.client.post("/api/auth/login", json={
            "email": email, "password": password, "device_id": device,
        })

    async def _count(self, model, *where):
        async with self.sessions() as db:
            return await db.scalar(select(func.count()).select_from(model).where(*where))

    async def _reset_token(self):
        token = "synthetic-reset-token"
        async with self.sessions() as db:
            db.add(PasswordReset(staff_id=self.teacher_id, reset_token=token,
                                 token_expires=auth._now() + timedelta(minutes=15)))
            await db.commit()
        return token

    async def _otp(self, purpose="login", *, attempts=0):
        async with self.sessions() as db:
            db.add(OTPCode(staff_id=self.teacher_id, code="123456", purpose=purpose,
                           expires_at=auth._now() + timedelta(minutes=5), attempts=attempts))
            await db.commit()

    async def test_new_jwts_are_unique_even_with_identical_second_and_claims(self):
        with patch.object(security, "datetime") as clock:
            clock.now.return_value = datetime.now(timezone.utc)
            first = security.create_access_token({"sub": str(self.teacher_id), "role": "teacher"})
            second = security.create_access_token({"sub": str(self.teacher_id), "role": "teacher"})
        self.assertNotEqual(first, second)
        self.assertNotEqual(security.decode_token(first)["jti"], security.decode_token(second)["jti"])

    async def test_trusted_login_started_before_correction_cannot_issue_session(self):
        async def during_hash(*args):
            self.assertFalse(_SESSION.get().in_transaction())
            await self._complete_correction()
            return True
        with patch.object(auth, "async_verify_password", side_effect=during_hash):
            response = await self._login()
        self.assertEqual(response.status_code, 401, response.text)
        self.assertEqual(await self._count(StaffSession, StaffSession.is_active.is_(True)), 0)

    async def test_wrong_password_started_before_correction_does_not_lock_corrected_account(self):
        async def during_hash(*args):
            await self._complete_correction()
            return False
        with patch.object(auth, "async_verify_password", side_effect=during_hash):
            response = await self._login(password="WrongPassword1!")
        self.assertEqual(response.status_code, 401, response.text)
        async with self.sessions() as db:
            self.assertEqual((await db.get(Staff, self.teacher_id)).login_attempts, 0)

    async def test_login_mail_in_flight_cannot_create_old_inbox_otp_after_correction(self):
        async def during_mail(email, *args, **kwargs):
            self.assertEqual(email, "teacher@example.com")
            self.assertFalse(_SESSION.get().in_transaction())
            await self._complete_correction()
        with patch.object(auth, "send_otp_email", side_effect=during_mail):
            response = await self._login(device="new-device")
        self.assertEqual(response.status_code, 401, response.text)
        self.assertEqual(await self._count(OTPCode, OTPCode.is_used.is_(False)), 0)

    async def test_reset_mail_in_flight_cannot_create_old_inbox_otp_after_correction(self):
        async def during_mail(*args, **kwargs):
            self.assertFalse(_SESSION.get().in_transaction())
            await self._complete_correction()
        with patch.object(auth, "send_otp_email", side_effect=during_mail):
            response = await self.client.post("/api/auth/forgot-password", json={"email": "teacher@example.com"})
        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(await self._count(OTPCode, OTPCode.is_used.is_(False)), 0)
        self.assertEqual(await self._count(PasswordReset), 0)

    async def test_login_code_waiting_for_staff_lock_is_revoked_before_session_creation(self):
        await self._otp()
        original = auth._lock_auth_staff
        async def before_lock(db, staff_id):
            await self._complete_correction()
            return await original(db, staff_id)
        with patch.object(auth, "_lock_auth_staff", side_effect=before_lock):
            response = await self.client.post("/api/auth/verify-otp", json={
                "user_id": str(self.teacher_id), "code": "123456", "device_id": "new-device",
            })
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(await self._count(StaffSession, StaffSession.is_active.is_(True)), 0)

    async def test_reset_code_waiting_for_staff_lock_cannot_create_reset_token(self):
        await self._otp("password-reset")
        original = auth._lock_auth_staff
        async def before_lock(db, staff_id):
            await self._complete_correction()
            return await original(db, staff_id)
        with patch.object(auth, "_lock_auth_staff", side_effect=before_lock):
            response = await self.client.post("/api/auth/verify-reset-otp", json={"email": "teacher@example.com", "code": "123456"})
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(await self._count(PasswordReset, PasswordReset.reset_token.is_not(None)), 0)

    async def test_reset_code_attempt_limit_rejects_even_the_correct_code(self):
        await self._otp("password-reset", attempts=settings.OTP_MAX_ATTEMPTS)
        response = await self.client.post("/api/auth/verify-reset-otp", json={"email": "teacher@example.com", "code": "123456"})
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(await self._count(PasswordReset, PasswordReset.reset_token.is_not(None)), 0)

    async def test_reset_password_hash_in_flight_cannot_change_corrected_account(self):
        token = await self._reset_token()
        async def during_hash(*args):
            self.assertFalse(_SESSION.get().in_transaction())
            await self._complete_correction()
            return "attacker-hash"
        with patch.object(auth, "async_get_password_hash", side_effect=during_hash):
            response = await self.client.post("/api/auth/reset-password", json={"token": token, "new_password": "NewPassword1!", "confirm_password": "NewPassword1!"})
        self.assertEqual(response.status_code, 400, response.text)
        async with self.sessions() as db:
            self.assertEqual((await db.get(Staff, self.teacher_id)).password_hash, "old-hash")

    async def test_own_password_hash_in_flight_cannot_change_corrected_account(self):
        async def during_hash(*args):
            self.assertFalse(_SESSION.get().in_transaction())
            await self._complete_correction()
            return "attacker-hash"
        with patch.object(staff_routes, "async_get_password_hash", side_effect=during_hash):
            response = await self.client.post("/api/staff/me/password", headers={"Authorization": f"Bearer {self.old_access}"},
                                              json={"current_password": "OldPassword1!", "new_password": "NewPassword1!"})
        self.assertEqual(response.status_code, 401, response.text)
        async with self.sessions() as db:
            self.assertEqual((await db.get(Staff, self.teacher_id)).password_hash, "old-hash")

    async def test_refresh_admitted_before_correction_is_rechecked_after_staff_lock(self):
        original = auth._lock_auth_staff
        async def before_lock(db, staff_id):
            await self._complete_correction()
            return await original(db, staff_id)
        with patch.object(auth, "_lock_auth_staff", side_effect=before_lock):
            response = await self.client.post("/api/auth/refresh", headers={"Authorization": f"Bearer {self.old_refresh}"})
        self.assertEqual(response.status_code, 401, response.text)
        self.assertEqual(await self._count(StaffSession, StaffSession.is_active.is_(True)), 0)

    async def test_refresh_rotates_only_the_session_for_the_presented_token(self):
        other_access = security.create_access_token({"sub": str(self.teacher_id), "role": "teacher"})
        other_refresh = security.create_refresh_token({"sub": str(self.teacher_id)})
        async with self.sessions() as db:
            db.add(StaffSession(staff_id=self.teacher_id, device_id=self.device_id, access_token=other_access,
                                refresh_token=other_refresh, expires_at=auth._now() + timedelta(minutes=15)))
            await db.commit()
        response = await self.client.post("/api/auth/refresh", headers={"Authorization": f"Bearer {self.old_refresh}"})
        self.assertEqual(response.status_code, 200, response.text)
        async with self.sessions() as db:
            untouched = await db.scalar(select(StaffSession).where(StaffSession.refresh_token == other_refresh))
            self.assertIsNotNone(untouched)
            self.assertEqual(untouched.access_token, other_access)
            self.assertEqual(await db.scalar(select(func.count()).select_from(StaffSession).where(
                StaffSession.refresh_token == response.json()["refresh_token"])), 1)

    async def test_untracked_old_tokens_are_rejected_after_correction_and_fresh_login_works(self):
        untracked_access = security.create_access_token({"sub": str(self.teacher_id), "role": "teacher"})
        untracked_refresh = security.create_refresh_token({"sub": str(self.teacher_id)})
        self.assertEqual((await self.client.get("/api/auth/me", headers={"Authorization": f"Bearer {untracked_access}"})).status_code, 200)
        await self._complete_correction()
        self.assertEqual((await self.client.get("/api/auth/me", headers={"Authorization": f"Bearer {untracked_access}"})).status_code, 401)
        self.assertEqual((await self.client.post("/api/auth/refresh", headers={"Authorization": f"Bearer {untracked_refresh}"})).status_code, 401)
        login = await self._login(email="corrected@example.com")
        self.assertEqual(login.status_code, 201, login.text)
        self.assertTrue(login.json()["requires_2fa"])
        async with self.sessions() as db:
            otp = await db.scalar(select(OTPCode).where(OTPCode.is_used.is_(False)))
        verified = await self.client.post("/api/auth/verify-otp", json={"user_id": str(self.teacher_id), "code": otp.code, "device_id": "known-device"})
        self.assertEqual(verified.status_code, 201, verified.text)
        access = verified.json()["access_token"]
        self.assertEqual((await self.client.get("/api/auth/me", headers={"Authorization": f"Bearer {access}"})).status_code, 200)

    async def test_ordinary_password_change_and_reset_still_work_without_open_hash_transaction(self):
        response = await self.client.post("/api/staff/me/password", headers={"Authorization": f"Bearer {self.old_access}"},
                                          json={"current_password": "OldPassword1!", "new_password": "NewPassword1!"})
        self.assertEqual(response.status_code, 200, response.text)
        token = await self._reset_token()
        response = await self.client.post("/api/auth/reset-password", json={"token": token, "new_password": "OtherPassword1!", "confirm_password": "OtherPassword1!"})
        self.assertEqual(response.status_code, 200, response.text)


@unittest.skipUnless(TEST_POSTGRES_URL, "Set TRACE_TEST_POSTGRES_URL for disposable PostgreSQL races")
class EmailCorrectionAuthPostgresTests(EmailCorrectionAuthTests):
    postgres_url = TEST_POSTGRES_URL

    async def test_otp_session_commit_before_correction_is_then_revoked_without_deadlock(self):
        await self._otp()
        entered, release = asyncio.Event(), asyncio.Event()
        original = auth._issue_session
        async def pause_before_commit(db, staff, device):
            entered.set()
            await release.wait()
            return await original(db, staff, device)
        with patch.object(auth, "_issue_session", side_effect=pause_before_commit):
            verify = asyncio.create_task(self.client.post("/api/auth/verify-otp", json={
                "user_id": str(self.teacher_id), "code": "123456", "device_id": "known-device",
            }))
            await asyncio.wait_for(entered.wait(), timeout=5)
            correction = asyncio.create_task(self._complete_correction())
            await asyncio.sleep(0.1)
            self.assertFalse(correction.done())
            release.set()
            result, _ = await asyncio.wait_for(asyncio.gather(verify, correction), timeout=10)
        self.assertEqual(result.status_code, 201, result.text)
        self.assertEqual(await self._count(StaffSession, StaffSession.is_active.is_(True)), 0)
