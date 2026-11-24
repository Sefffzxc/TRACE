"""Required temporary-password changes use real auth against isolated SQLite."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bcrypt
from fastapi import FastAPI
import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models
from app.api import deps
from app.api.routers import auth, dashboard, staff
from app.core.config import settings
from app.core.security import create_access_token, verify_password
from app.db.session import Base, get_db
from app.models.schools import School
from app.models.staff import OTPCode, Staff, StaffDevice


PASSWORD = "SyntheticOriginal1!"
NEW_PASSWORD = "SyntheticReplacement2!"


class RequiredPasswordChangeTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.password_hash = bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt(rounds=4)).decode()

    async def asyncSetUp(self):
        configuration = patch.multiple(
            settings, SECRET_KEY="synthetic-required-password-test-secret",
            EMAIL_ENABLED=False, DEBUG_OTP=False, LOG_OTP_CODES=False, CAPTCHA_ENABLED=False,
        )
        configuration.start()
        self.addCleanup(configuration.stop)
        deps._BUCKETS.clear()
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False, autoflush=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.admin_id, self.teacher_id = uuid4(), uuid4()
        async with self.sessions() as db:
            db.add(School(school_id=1, name="Synthetic School", status="approved"))
            await db.flush()
            for identity, role in ((self.admin_id, "admin"), (self.teacher_id, "teacher")):
                db.add(Staff(
                    id=identity, school_id=1, role=role,
                    email=f"{role}@example.com", password_hash=self.password_hash,
                    first_name="Synthetic", last_name=role, contact_number="",
                    account_status="approved", is_approved=True, is_active=True,
                ))
            await db.flush()
            db.add(StaffDevice(
                staff_id=self.teacher_id, device_id="trusted-test-device", is_trusted=True,
                last_2fa_verified=datetime.now(timezone.utc),
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
        for router in (auth.router, staff.router, dashboard.router):
            application.include_router(router)
        application.dependency_overrides[get_db] = isolated_db
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application), base_url="http://testserver",
        )
        self.admin_headers = {"Authorization": f"Bearer {create_access_token({'sub': str(self.admin_id)})}"}
        self.mail = patch.object(auth, "send_otp_email", new_callable=AsyncMock)
        self.send_otp = self.mail.start()
        self.addCleanup(self.mail.stop)

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.engine.dispose()
        deps._BUCKETS.clear()

    async def issue_temporary_password(self):
        response = await self.client.patch(
            f"/api/staff/{self.teacher_id}", json={"action": "force_reset"},
            headers=self.admin_headers,
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["temporary_password"]

    async def login(self, password, device="trusted-test-device"):
        response = await self.client.post("/api/auth/login", json={
            "email": "teacher@example.com", "password": password, "device_id": device,
        })
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def headers(self, login):
        return {"Authorization": f"Bearer {login['access_token']}"}

    async def complete(self, headers, password=NEW_PASSWORD, confirm=None):
        return await self.client.post("/api/staff/me/required-password", headers=headers, json={
            "new_password": password, "confirm_password": password if confirm is None else confirm,
        })

    async def test_temporary_login_cannot_use_features_until_password_changes(self):
        temporary = await self.issue_temporary_password()
        login = await self.login(temporary)
        self.assertTrue(login["user"]["requires_password_change"])
        headers = self.headers(login)
        for path in ("/api/dashboard", "/api/staff"):
            response = await self.client.get(path, headers=headers)
            self.assertEqual(response.status_code, 403, response.text)
            self.assertEqual(response.json()["detail"]["code"], "password_change_required")
        response = await self.client.patch("/api/staff/me", headers=headers, json={"first_name": "Changed"})
        self.assertEqual(response.status_code, 403)
        for path in ("/api/auth/me", "/api/staff/me"):
            response = await self.client.get(path, headers=headers)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertTrue(response.json()["requires_password_change"])
        response = await self.complete(headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()["user"]["requires_password_change"])
        self.assertEqual((await self.client.get("/api/dashboard", headers=headers)).status_code, 200)
        async with self.sessions() as db:
            teacher = await db.get(Staff, self.teacher_id)
            self.assertTrue(verify_password(NEW_PASSWORD, teacher.password_hash))
            self.assertFalse(verify_password(temporary, teacher.password_hash))
        self.assertEqual((await self.complete(headers)).status_code, 409)

    async def test_rejected_passwords_do_not_clear_the_requirement(self):
        temporary = await self.issue_temporary_password()
        headers = self.headers(await self.login(temporary))
        for password, confirm, expected in (
            (NEW_PASSWORD, "DifferentSynthetic3!", 422),
            ("weakpass", "weakpass", 422),
            (temporary, temporary, 400),
        ):
            with self.subTest(expected=expected):
                response = await self.complete(headers, password, confirm)
                self.assertEqual(response.status_code, expected, response.text)
                async with self.sessions() as db:
                    teacher = await db.get(Staff, self.teacher_id)
                    self.assertTrue(teacher.requires_password_change)
                    self.assertTrue(verify_password(temporary, teacher.password_hash))

    async def test_unknown_device_completes_otp_before_required_password_change(self):
        async with self.sessions() as db:
            db.add(OTPCode(staff_id=self.teacher_id, purpose="login", code="123456",
                           expires_at=datetime.now(timezone.utc) + timedelta(minutes=5)))
            await db.commit()
        temporary = await self.issue_temporary_password()
        stale = await self.client.post("/api/auth/verify-otp", json={
            "user_id": str(self.teacher_id), "code": "123456", "device_id": "new-test-device",
        })
        self.assertEqual(stale.status_code, 400, stale.text)
        login = await self.login(temporary, device="new-test-device")
        self.assertTrue(login["requires_2fa"])
        self.assertIsNone(login.get("access_token"))
        self.send_otp.assert_awaited_once()
        async with self.sessions() as db:
            otp = await db.scalar(select(OTPCode).where(OTPCode.staff_id == self.teacher_id,
                                                       OTPCode.is_used.is_(False)))
            code = otp.code
        response = await self.client.post("/api/auth/verify-otp", json={
            "user_id": str(self.teacher_id), "code": code, "device_id": "new-test-device",
        })
        self.assertEqual(response.status_code, 201, response.text)
        headers = self.headers(response.json())
        self.assertEqual((await self.client.get("/api/dashboard", headers=headers)).status_code, 403)
        self.assertEqual((await self.complete(headers)).status_code, 200)
        self.assertEqual((await self.client.get("/api/dashboard", headers=headers)).status_code, 200)

    async def test_pre_reset_tokens_cannot_replace_temporary_password_sign_in(self):
        previous = await self.login(PASSWORD)
        temporary = await self.issue_temporary_password()
        old_headers = self.headers(previous)
        self.assertEqual((await self.client.get("/api/dashboard", headers=old_headers)).status_code, 403)
        self.assertEqual((await self.complete(old_headers)).status_code, 401)
        response = await self.client.post("/api/auth/refresh", headers={
            "Authorization": f"Bearer {previous['refresh_token']}",
        })
        self.assertEqual(response.status_code, 401, response.text)
        login = await self.login(temporary)
        response = await self.client.post("/api/auth/refresh", headers={
            "Authorization": f"Bearer {login['refresh_token']}",
        })
        self.assertEqual(response.status_code, 200, response.text)
        headers = self.headers(response.json())
        self.assertEqual((await self.client.get("/api/dashboard", headers=headers)).status_code, 403)
        self.assertEqual((await self.complete(headers)).status_code, 200)

    async def test_normal_account_cannot_use_required_password_endpoint(self):
        headers = self.headers(await self.login(PASSWORD))
        self.assertEqual((await self.client.get("/api/dashboard", headers=headers)).status_code, 200)
        self.assertEqual((await self.complete(headers)).status_code, 409)

    async def test_flagged_account_can_log_out(self):
        headers = self.headers(await self.login(await self.issue_temporary_password()))
        response = await self.client.post("/api/auth/logout", headers=headers)
        self.assertEqual(response.status_code, 204, response.text)
        self.assertEqual((await self.complete(headers)).status_code, 401)

    async def test_new_reset_during_hashing_does_not_get_overwritten(self):
        headers = self.headers(await self.login(await self.issue_temporary_password()))
        replacement = bcrypt.hashpw("AnotherTemporary3!".encode(), bcrypt.gensalt(rounds=4)).decode()

        async def reset_during_hashing(_password):
            async with self.sessions() as db:
                teacher = await db.get(Staff, self.teacher_id)
                teacher.password_hash = replacement
                teacher.requires_password_change = True
                await db.commit()
            return self.password_hash

        with patch.object(staff, "async_get_password_hash", side_effect=reset_during_hashing):
            response = await self.complete(headers)
        self.assertEqual(response.status_code, 401, response.text)
        async with self.sessions() as db:
            teacher = await db.get(Staff, self.teacher_id)
            self.assertEqual(teacher.password_hash, replacement)
            self.assertTrue(teacher.requires_password_change)


if __name__ == "__main__":
    unittest.main()
