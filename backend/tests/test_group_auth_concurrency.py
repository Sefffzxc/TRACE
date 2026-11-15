"""Group sign-in and registration regressions against an isolated small pool.

Run from backend: python -m unittest discover -s tests -p test_group_auth_concurrency.py
Only synthetic accounts, a temporary SQLite database and fake email are used.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bcrypt
from fastapi import FastAPI
import httpx
from sqlalchemy import func, select
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import AsyncAdaptedQueuePool

import app.models
from app.api import deps
from app.api.routers import auth, staff
from app.core.config import settings
from app.core.security import create_access_token
from app.db.session import Base, get_db
from app.models.schools import School
from app.models.staff import OTPCode, Staff, StaffSession
from app.services.login_captcha import login_risk


PASSWORD = "SyntheticPassword1!"
GROUP_SIZE = 13
SHARED_IP = "198.51.100.42"


class GroupAuthenticationTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.password_hash = bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt(rounds=4)).decode()

    async def asyncSetUp(self):
        configuration = patch.multiple(
            settings, CAPTCHA_ENABLED=False, DEBUG_OTP=False, LOG_OTP_CODES=False,
            OTP_EXPIRY_MINUTES=5, SECRET_KEY="synthetic-group-test-jwt-secret",
        )
        configuration.start()
        self.addCleanup(configuration.stop)
        deps._BUCKETS.clear()
        login_risk.clear()
        self.temporary_directory = tempfile.TemporaryDirectory(prefix="trace-group-auth-")
        self.addCleanup(self.temporary_directory.cleanup)
        database_path = Path(self.temporary_directory.name) / "isolated.sqlite3"
        self.engine = create_async_engine(
            URL.create("sqlite+aiosqlite", database=str(database_path)),
            poolclass=AsyncAdaptedQueuePool,
            pool_size=2, max_overflow=0, pool_timeout=3,
            connect_args={"timeout": 10},
        )
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False, autoflush=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        self.login_emails = [f"group-login-{index}@example.com" for index in range(GROUP_SIZE)]
        self.admin_ids = {school_id: uuid4() for school_id in (1, 2)}
        async with self.sessions() as db:
            db.add_all([
                School(school_id=1, name="Synthetic Group School", status="approved"),
                School(school_id=2, name="Synthetic Other School", status="approved"),
            ])
            await db.flush()
            for school_id in (1, 2):
                db.add(self._person(
                    f"group-admin-{school_id}@example.com", school_id=school_id,
                    identity=self.admin_ids[school_id], role="admin",
                ))
                db.add(self._person(
                    f"existing-pending-{school_id}@example.com", school_id=school_id,
                    approved=False,
                ))
            db.add_all([self._person(email) for email in self.login_emails])
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
        application.include_router(staff.router)
        application.dependency_overrides[get_db] = isolated_db
        # Use the actual rate limit dependencies: school Wi-Fi shares one IP.
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application, client=(SHARED_IP, 45000)),
            base_url="http://testserver",
        )
        self.admin_headers = {
            school_id: {"Authorization": f"Bearer {create_access_token({'sub': str(identity)})}"}
            for school_id, identity in self.admin_ids.items()
        }

    def _person(self, email, *, school_id=1, identity=None, role="teacher", approved=True):
        return Staff(
            id=identity or uuid4(), school_id=school_id, email=email,
            password_hash=self.password_hash, first_name="Synthetic", last_name="Staff",
            contact_number="09123456789", role=role, is_active=True,
            is_approved=approved, account_status="approved" if approved else "pending",
        )

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.engine.dispose()
        self.temporary_directory.cleanup()
        deps._BUCKETS.clear()
        login_risk.clear()

    async def _count(self, model):
        async with self.sessions() as db:
            return (await db.execute(select(func.count()).select_from(model))).scalar_one()

    async def _pending_emails(self, school_id=1):
        response = await asyncio.wait_for(
            self.client.get("/api/staff/pending", headers=self.admin_headers[school_id]),
            timeout=2,
        )
        self.assertEqual(response.status_code, 200, response.text)
        return {person["email"] for person in response.json()}

    async def test_thirteen_waiting_otp_emails_leave_small_pool_available_to_admin(self):
        all_entered = asyncio.Event()
        release_email = asyncio.Event()
        recipients = []

        async def paused_email(email, code, *, purpose):
            self.assertEqual(purpose, "login")
            recipients.append(email)
            if len(recipients) == GROUP_SIZE:
                all_entered.set()
            await release_email.wait()

        responses = []
        with patch.object(auth, "async_verify_password", new_callable=AsyncMock, return_value=True), \
                patch.object(auth, "send_otp_email", side_effect=paused_email):
            requests = [asyncio.create_task(self.client.post("/api/auth/login", json={
                "email": email, "password": PASSWORD, "device_id": f"new-group-device-{index}",
            })) for index, email in enumerate(self.login_emails)]
            try:
                await asyncio.wait_for(all_entered.wait(), timeout=5)
                self.assertEqual(self.engine.pool.checkedout(), 0)
                self.assertEqual(set(recipients), set(self.login_emails))
                self.assertTrue(all(not request.done() for request in requests))
                self.assertEqual(await self._pending_emails(), {"existing-pending-1@example.com"})
                self.assertEqual(await self._count(OTPCode), 0)
                self.assertEqual(await self._count(StaffSession), 0)
            finally:
                before_delivery = datetime.now(timezone.utc)
                release_email.set()
                responses = await asyncio.wait_for(asyncio.gather(*requests), timeout=10)

        completed = datetime.now(timezone.utc)
        for response in responses:
            self.assertEqual(response.status_code, 201, response.text)
            self.assertTrue(response.json()["requires_2fa"])
            self.assertFalse(response.json().get("access_token"))
            self.assertFalse(response.json().get("refresh_token"))
        self.assertEqual(await self._count(StaffSession), 0)
        async with self.sessions() as db:
            codes = (await db.execute(select(OTPCode))).scalars().all()
            self.assertEqual(len(codes), GROUP_SIZE)
            self.assertEqual(len({code.staff_id for code in codes}), GROUP_SIZE)
            for code in codes:
                expiry = auth._aware(code.expires_at)
                self.assertFalse(code.is_used)
                self.assertGreaterEqual(expiry, before_delivery + timedelta(minutes=5))
                self.assertLessEqual(expiry, completed + timedelta(minutes=5))

    async def test_thirteen_same_network_registrations_are_immediately_visible_to_own_admin(self):
        emails = {f"group-register-{index}@example.com" for index in range(GROUP_SIZE)}
        with patch.object(
            auth, "async_get_password_hash", new_callable=AsyncMock, return_value=self.password_hash,
        ):
            responses = await asyncio.wait_for(asyncio.gather(*[
                self.client.post("/api/auth/register", json={
                    "school_id": 1, "email": email, "password": PASSWORD,
                    "first_name": "Synthetic", "last_name": "Applicant",
                    "contact_number": "09123456789",
                }) for email in sorted(emails)
            ]), timeout=10)
        for response in responses:
            self.assertEqual(response.status_code, 201, response.text)
            self.assertEqual(response.json()["account_status"], "pending")
            self.assertFalse(response.json()["is_approved"])
        self.assertEqual(await self._pending_emails(), emails | {"existing-pending-1@example.com"})
        self.assertEqual(await self._pending_emails(2), {"existing-pending-2@example.com"})


if __name__ == "__main__":
    unittest.main()
