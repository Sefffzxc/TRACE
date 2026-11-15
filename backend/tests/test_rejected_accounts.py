"""Rejected registration visibility and sign-in regressions.

Run from backend: python -m unittest discover -s tests -p test_rejected_accounts.py
Uses an isolated in-memory database, real password checks and fake email only.
"""
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
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models
from app.api import deps
from app.api.routers import auth, staff
from app.core.config import settings
from app.core.security import create_access_token, create_refresh_token
from app.db.session import Base, get_db
from app.models.schools import School
from app.models.staff import OTPCode, Staff, StaffSession


PASSWORD = "TestPassword1!"
REASON = "Please correct your employee number."


class RejectedAccountTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.password_hash = bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt(rounds=4)).decode()

    async def asyncSetUp(self):
        self.configuration = patch.multiple(
            settings, SECRET_KEY="test-rejection-secret-not-a-production-key",
            EMAIL_ENABLED=False, DEBUG_OTP=False, LOG_OTP_CODES=False, CAPTCHA_ENABLED=False,
        )
        self.configuration.start()
        self.addCleanup(self.configuration.stop)
        deps._BUCKETS.clear()
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False, autoflush=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.people = {}
        async with self.sessions() as db:
            db.add_all([
                School(school_id=1, name="Our Test School", status="approved"),
                School(school_id=2, name="Other Test School", status="approved"),
            ])
            await db.flush()
            for name, account_status, approved, active, school_id, role in (
                ("Admin", "approved", True, True, 1, "admin"),
                ("Approved", "approved", True, True, 1, "teacher"),
                ("Pending", "pending", False, True, 1, "teacher"),
                ("Rejected", "rejected", False, True, 1, "teacher"),
                ("RejectedLegacy", "rejected", True, True, 1, "teacher"),
                ("Deactivated", "deactivated", True, False, 1, "teacher"),
                ("OtherRejected", "rejected", False, True, 2, "teacher"),
                ("OtherApproved", "approved", True, True, 2, "teacher"),
            ):
                person = Staff(
                    id=uuid4(), school_id=school_id, role=role,
                    email=f"{name.lower()}@example.com", password_hash=self.password_hash,
                    first_name=name, last_name="Teacher", contact_number="",
                    account_status=account_status, is_approved=approved, is_active=active,
                    rejection_reason=REASON if account_status == "rejected" else None,
                )
                db.add(person)
                self.people[name] = person
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
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application), base_url="http://testserver",
        )
        admin_token = create_access_token({"sub": str(self.people["Admin"].id)})
        self.admin_headers = {"Authorization": f"Bearer {admin_token}"}

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.engine.dispose()
        deps._BUCKETS.clear()

    async def _login(self, name, password=PASSWORD):
        return await self.client.post("/api/auth/login", json={
            "email": self.people[name].email, "password": password, "device_id": "test-device",
        })

    async def _count(self, model):
        async with self.sessions() as db:
            return (await db.execute(select(func.count()).select_from(model))).scalar_one()

    async def test_default_directory_and_search_hide_rejected_accounts(self):
        for params in ({}, {"role": "teacher"}, {"search": "rejected"}):
            with self.subTest(params=params):
                response = await self.client.get("/api/staff", params=params, headers=self.admin_headers)
                self.assertEqual(response.status_code, 200, response.text)
                rows = response.json()
                self.assertFalse(any(row["account_status"] == "rejected" for row in rows))
                school_ids = {str(person.id) for person in self.people.values() if person.school_id == 1}
                self.assertTrue({row["id"] for row in rows}.issubset(school_ids))
                if params == {"role": "teacher"}:
                    self.assertEqual({row["first_name"] for row in rows}, {"Approved", "Pending", "Deactivated"})
                if "search" in params:
                    self.assertEqual(rows, [])

    async def test_rejected_registration_remains_on_file_and_scoped_explicit_review_works(self):
        response = await self.client.get(
            "/api/staff", params={"account_status": "rejected"}, headers=self.admin_headers,
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual({row["first_name"] for row in response.json()}, {"Rejected", "RejectedLegacy"})
        async with self.sessions() as db:
            person = await db.get(Staff, self.people["Rejected"].id)
            self.assertIsNotNone(person)
            self.assertEqual(person.rejection_reason, REASON)

    async def test_enriched_teacher_list_hides_rejection_even_with_legacy_approval_flag(self):
        response = await self.client.get("/api/staff/teachers", headers=self.admin_headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn("RejectedLegacy", {row["first_name"] for row in response.json()})
        self.assertIn("Approved", {row["first_name"] for row in response.json()})

    async def test_rejected_login_shows_decision_and_reason_without_creating_otp_or_session(self):
        with patch.object(auth, "send_otp_email", new_callable=AsyncMock) as send:
            for name in ("Rejected", "RejectedLegacy"):
                with self.subTest(name=name):
                    response = await self._login(name)
                    self.assertEqual(response.status_code, 403, response.text)
                    detail = response.json()["detail"]
                    self.assertEqual(detail["code"], "application_rejected")
                    self.assertIs(detail["can_resubmit"], True)
                    self.assertIn("rejected", detail["message"])
                    self.assertIn(REASON, detail["message"])
                    self.assertIn("school administrator", detail["message"])
                    self.assertNotIn("awaiting", detail["message"])
                    self.assertNotIn("access_token", response.json())
            send.assert_not_awaited()
        self.assertEqual(await self._count(OTPCode), 0)
        self.assertEqual(await self._count(StaffSession), 0)

    async def test_missing_rejection_reason_has_a_clear_fallback(self):
        async with self.sessions() as db:
            person = await db.get(Staff, self.people["Rejected"].id)
            person.rejection_reason = None
            await db.commit()
        response = await self._login("Rejected")
        self.assertEqual(response.status_code, 403, response.text)
        self.assertIn("has been rejected", response.json()["detail"]["message"])
        self.assertNotIn("None", response.json()["detail"]["message"])

    async def test_wrong_password_does_not_disclose_rejection_or_reason(self):
        response = await self._login("Rejected", "WrongPassword1!")
        self.assertEqual(response.status_code, 401, response.text)
        self.assertIn("Invalid email or password", response.json()["detail"])
        self.assertNotIn(REASON, response.text)
        self.assertNotIn("rejected", response.text.lower())
        self.assertNotIn("can_resubmit", response.text)
        async with self.sessions() as db:
            person = await db.get(Staff, self.people["Rejected"].id)
            self.assertEqual(person.login_attempts, 1)

    async def test_pending_and_deactivated_accounts_keep_their_correct_messages(self):
        for name, message in (("Pending", "awaiting administrator approval"), ("Deactivated", "deactivated")):
            with self.subTest(name=name):
                response = await self._login(name)
                self.assertEqual(response.status_code, 403, response.text)
                self.assertIn(message, response.json()["detail"])
                self.assertNotIn("rejected", response.text)
                self.assertNotIn("can_resubmit", response.text)

    async def test_login_resubmission_hint_requires_correct_credentials_and_an_eligible_application(self):
        cases = (
            ("PrincipalRejected", "admin", 3, "rejected", True, True),
            ("PrincipalApprovedSchool", "admin", 1, "rejected", True, False),
            ("PrincipalPendingSchool", "admin", 4, "rejected", True, False),
            ("PrincipalBlockedSchool", "admin", 5, "rejected", True, False),
            ("InactiveRejected", "teacher", 1, "rejected", False, False),
            ("Blocked", "teacher", 1, "blocked", False, False),
            ("PendingApplicant", "teacher", 1, "pending", True, False),
            ("DeactivatedApplicant", "teacher", 1, "deactivated", False, False),
        )
        async with self.sessions() as db:
            db.add_all([
                School(school_id=3, name="Rejected School", status="rejected"),
                School(school_id=4, name="Pending School", status="pending"),
                School(school_id=5, name="Blocked School", status="blocked"),
            ])
            await db.flush()
            for name, role, school_id, account_status, active, _ in cases:
                person = Staff(
                    id=uuid4(), school_id=school_id, email=f"{name.lower()}@example.com",
                    role=role, first_name=name, last_name="Applicant", contact_number="09123456789",
                    password_hash=self.password_hash, account_status=account_status,
                    is_active=active, is_approved=False,
                    rejection_reason=REASON if account_status == "rejected" else None,
                )
                db.add(person)
                self.people[name] = person
            await db.commit()
        with patch.object(auth, "send_otp_email", new_callable=AsyncMock) as send:
            with patch.object(auth, "_token_bundle") as issue_tokens:
                for name, _, _, _, _, eligible in cases:
                    with self.subTest(name=name):
                        response = await self._login(name)
                        self.assertEqual(response.status_code, 403, response.text)
                        detail = response.json()["detail"]
                        if eligible:
                            self.assertEqual(detail["code"], "application_rejected")
                            self.assertIs(detail["can_resubmit"], True)
                            self.assertIn(REASON, detail["message"])
                            self.assertIn("system operator", detail["message"])
                        else:
                            self.assertIsInstance(detail, str)
                            self.assertNotIn("can_resubmit", response.text)
                        self.assertNotIn("access_token", response.json())
                        self.assertNotIn("refresh_token", response.json())
                for name in ("Rejected", "PrincipalRejected"):
                    response = await self._login(name, "WrongPassword1!")
                    self.assertEqual(response.status_code, 401, response.text)
                    self.assertNotIn("can_resubmit", response.text)
                    self.assertNotIn(REASON, response.text)
                response = await self.client.post("/api/auth/login", json={
                    "email": "unknown@example.com", "password": PASSWORD, "device_id": "test-device",
                })
                self.assertEqual(response.status_code, 401, response.text)
                self.assertNotIn("can_resubmit", response.text)
                self.assertNotIn(REASON, response.text)
                issue_tokens.assert_not_called()
            send.assert_not_awaited()
        self.assertEqual(await self._count(OTPCode), 0)
        self.assertEqual(await self._count(StaffSession), 0)

    async def test_rejection_through_admin_action_hides_teacher_and_updates_login(self):
        target = self.people["Approved"]
        with patch.object(staff, "send_account_decision_email", new_callable=AsyncMock) as send:
            response = await self.client.patch(
                f"/api/staff/{target.id}", json={"action": "reject", "reason": REASON}, headers=self.admin_headers,
            )
        self.assertEqual(response.status_code, 200, response.text)
        send.assert_awaited_once()
        directory = await self.client.get("/api/staff", headers=self.admin_headers)
        self.assertNotIn(str(target.id), {row["id"] for row in directory.json()})
        response = await self._login("Approved")
        self.assertEqual(response.status_code, 403, response.text)
        self.assertIn(REASON, response.json()["detail"]["message"])
        self.assertIn("rejected", response.json()["detail"]["message"])

    async def test_valid_otp_issued_before_rejection_cannot_create_a_session(self):
        person = self.people["Rejected"]
        async with self.sessions() as db:
            db.add(OTPCode(
                staff_id=person.id, code="483927", purpose="login",
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
            ))
            await db.commit()
        response = await self.client.post("/api/auth/verify-otp", json={
            "user_id": str(person.id), "code": "483927", "device_id": "test-device",
        })
        self.assertEqual(response.status_code, 403, response.text)
        self.assertIn("rejected", response.json()["detail"])
        self.assertEqual(await self._count(StaffSession), 0)

    async def test_existing_access_and_refresh_tokens_respect_the_current_rejection(self):
        person = self.people["Approved"]
        access = create_access_token({"sub": str(person.id)})
        refresh = create_refresh_token({"sub": str(person.id)})
        async with self.sessions() as db:
            current = await db.get(Staff, person.id)
            current.account_status = "rejected"
            current.rejection_reason = REASON
            # Exercise the legacy case too: status must override this flag.
            current.is_approved = True
            await db.commit()
        for method, path, token in (("GET", "/api/auth/me", access), ("POST", "/api/auth/refresh", refresh)):
            with self.subTest(path=path):
                response = await self.client.request(method, path, headers={"Authorization": f"Bearer {token}"})
                self.assertEqual(response.status_code, 403, response.text)
                self.assertIn("rejected", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
