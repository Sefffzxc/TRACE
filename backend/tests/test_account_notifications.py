"""Principal/teacher decision regressions with fake email and an isolated DB.

Run from backend: python -m unittest discover -s tests -p test_account_notifications.py
No real messages or application database connections are made.
"""
from __future__ import annotations

from base64 import b64decode
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from uuid import uuid4

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import Depends, FastAPI
import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models
from app.api import deps
from app.api.routers import schools, staff
from app.core.config import settings
from app.db.session import Base, get_db
from app.models.schools import School
from app.models.staff import AuditLog, Staff
from app.services import email as email_service


HTTPClient = httpx.AsyncClient


class DecisionEmailTests(unittest.IsolatedAsyncioTestCase):
    async def test_each_role_and_decision_has_branded_escaped_content(self):
        with patch.multiple(
            settings, EMAIL_ENABLED=True, RESEND_API_KEY="fake-test-key",
            EMAIL_FROM="TRACE <noreply@example.com>", DEBUG_OTP=False, LOG_OTP_CODES=False,
        ):
            for role in ("admin", "teacher"):
                for decision in ("approved", "rejected"):
                    with self.subTest(role=role, decision=decision):
                        sent = []

                        def handle(request):
                            sent.append(json.loads(request.content))
                            return httpx.Response(200, json={"id": "fake-message-id"})

                        provider = HTTPClient(transport=httpx.MockTransport(handle))
                        with patch.object(email_service.httpx, "AsyncClient", return_value=provider):
                            await email_service.send_account_decision_email(
                                "applicant@example.com", full_name="Alex <Teacher>", role=role,
                                decision=decision, school_name="Test & Example School",
                                reason='<script>alert("test")</script>',
                            )
                        payload, = sent
                        self.assertEqual(payload["to"], ["applicant@example.com"])
                        self.assertIn(decision, payload["subject"])
                        self.assertIn("principal" if role == "admin" else "teacher", payload["text"])
                        self.assertIn("Alex &lt;Teacher&gt;", payload["html"])
                        self.assertIn("Test &amp; Example School", payload["html"])
                        self.assertNotIn("<script>", payload["html"])
                        self.assertIn("Reason:" if decision == "rejected" else "sign in", payload["text"])
                        if decision == "rejected":
                            self.assertIn("&lt;script&gt;", payload["html"])
                        logo, = payload["attachments"]
                        self.assertIn(f'src="cid:{logo["content_id"]}"', payload["html"])
                        self.assertTrue(b64decode(logo["content"]).startswith(b"\x89PNG\r\n\x1a\n"))


class AccountDecisionRouteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.configuration = patch.multiple(
            settings, EMAIL_ENABLED=True, RESEND_API_KEY="fake-route-key",
            EMAIL_FROM="TRACE <noreply@example.com>", DEBUG_OTP=False, LOG_OTP_CODES=False,
        )
        self.configuration.start()
        self.addCleanup(self.configuration.stop)
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False, autoflush=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with self.sessions() as db:
            db.add_all([
                School(school_id=1, name="Approved Test School", status="approved"),
                School(school_id=2, name="Applicant Test School", status="pending"),
            ])
            await db.flush()
            self.admin = self._staff("admin", 1, approved=True)
            self.teacher = self._staff("teacher", 1)
            self.principal = self._staff("admin", 2)
            self.operator = self._staff("super_admin", None, approved=True)
            db.add_all([self.admin, self.teacher, self.principal, self.operator])
            await db.commit()
        self.actor_id = self.admin.id

        async def isolated_db():
            async with self.sessions() as db:
                try:
                    yield db
                except Exception:
                    await db.rollback()
                    raise

        async def current_actor(db=Depends(get_db)):
            return await db.get(Staff, self.actor_id)

        app = FastAPI()
        app.include_router(staff.router)
        app.include_router(schools.router)
        app.dependency_overrides[get_db] = isolated_db
        app.dependency_overrides[deps.get_current_user] = current_actor
        self.client = HTTPClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")
        self.sent = []
        self.provider_status = 200

        def handle(request):
            self.sent.append(json.loads(request.content))
            return httpx.Response(self.provider_status, json={"id": "fake-message-id"})

        self.provider = patch.object(
            email_service.httpx, "AsyncClient",
            side_effect=lambda **kwargs: HTTPClient(transport=httpx.MockTransport(handle)),
        )
        self.provider.start()
        self.addCleanup(self.provider.stop)

    @staticmethod
    def _staff(role, school_id, *, approved=False):
        identity = uuid4()
        return Staff(
            id=identity, school_id=school_id, role=role, email=f"{identity}@example.com",
            password_hash="unused-test-hash", first_name="Test", last_name="Applicant",
            contact_number="", is_active=True, is_approved=approved,
            account_status="approved" if approved else "pending",
        )

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.engine.dispose()

    async def _staff_decision(self, action, target=None):
        return await self.client.patch(f"/api/staff/{(target or self.teacher).id}", json={
            "action": action, "reason": "Please correct your details.",
        })

    async def _school_decision(self, action):
        self.actor_id = self.operator.id
        return await self.client.patch("/api/schools/2", json={
            "action": action, "reason": "Please supply school verification.",
        })

    async def _assert_saved(self, identity, expected):
        async with self.sessions() as db:
            person = await db.get(Staff, identity)
            self.assertEqual(person.account_status, expected)
            self.assertEqual(person.is_approved, expected == "approved")
            self.assertEqual(person.rejection_reason is not None, expected == "rejected")

    async def test_teacher_acceptance_and_rejection_notify_the_registered_email(self):
        for action, expected in (("approve", "approved"), ("reject", "rejected")):
            with self.subTest(action=action):
                response = await self._staff_decision(action)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(self.sent[-1]["to"], [self.teacher.email])
                self.assertIn(expected, self.sent[-1]["subject"])
                self.assertIn("Approved Test School", self.sent[-1]["text"])
                if action == "reject":
                    self.assertIn("Please correct your details.", self.sent[-1]["text"])
                await self._assert_saved(self.teacher.id, expected)
        self.assertEqual(len(self.sent), 2)

    async def test_principal_acceptance_rejection_and_reapproval_notify_and_sync_state(self):
        for action, expected in (("approve", "approved"), ("reject", "rejected"), ("approve", "approved")):
            with self.subTest(action=action, expected=expected):
                response = await self._school_decision(action)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["status"], expected)
                self.assertEqual(self.sent[-1]["to"], [self.principal.email])
                self.assertIn(expected, self.sent[-1]["subject"])
                self.assertIn("principal", self.sent[-1]["text"])
                if action == "reject":
                    self.assertIn("Please supply school verification.", self.sent[-1]["text"])
                await self._assert_saved(self.principal.id, expected)
        self.assertEqual(len(self.sent), 3)

    async def test_restore_rejected_school_activates_principal_and_emails_approval(self):
        self.assertEqual((await self._school_decision("reject")).status_code, 200)
        response = await self._school_decision("reactivate")
        self.assertEqual(response.status_code, 200, response.text)
        await self._assert_saved(self.principal.id, "approved")
        self.assertIsNotNone(response.json()["approved_at"])
        async with self.sessions() as db:
            school = await db.get(School, 2)
            self.assertEqual(school.approved_by, self.operator.id)
        self.assertIn("approved", self.sent[-1]["subject"])
        self.assertEqual(len(self.sent), 2)

    async def test_restore_rejected_teacher_emails_approval(self):
        self.assertEqual((await self._staff_decision("reject")).status_code, 200)
        response = await self._staff_decision("reactivate")
        self.assertEqual(response.status_code, 200, response.text)
        await self._assert_saved(self.teacher.id, "approved")
        async with self.sessions() as db:
            person = await db.get(Staff, self.teacher.id)
            self.assertIsNotNone(person.approved_at)
            self.assertEqual(person.approved_by, self.admin.id)
        self.assertIn("approved", self.sent[-1]["subject"])

    async def test_all_principal_accounts_receive_the_school_decision(self):
        second = self._staff("admin", 2)
        async with self.sessions() as db:
            db.add(second)
            await db.commit()
        response = await self._school_decision("approve")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual({payload["to"][0] for payload in self.sent}, {self.principal.email, second.email})
        await self._assert_saved(second.id, "approved")

    async def test_email_failure_rolls_back_decision_and_audit_for_both_account_types(self):
        self.provider_status = 403
        for kind in ("teacher", "principal"):
            for action in ("approve", "reject"):
                with self.subTest(kind=kind, action=action):
                    self.actor_id = self.admin.id
                    response = await (self._staff_decision(action) if kind == "teacher" else self._school_decision(action))
                    self.assertEqual(response.status_code, 503, response.text)
                    self.assertIn("not saved", response.json()["detail"])
                    await self._assert_saved(self.teacher.id if kind == "teacher" else self.principal.id, "pending")
                    async with self.sessions() as db:
                        school = await db.get(School, 2)
                        self.assertEqual(school.status, "pending")
                        self.assertEqual((await db.execute(select(func.count()).select_from(AuditLog))).scalar_one(), 0)

    async def test_role_and_school_boundaries_prevent_decisions_and_emails(self):
        self.actor_id = self.teacher.id
        self.assertEqual((await self._staff_decision("approve")).status_code, 403)
        self.assertEqual((await self.client.patch("/api/schools/2", json={"action": "approve"})).status_code, 403)
        self.actor_id = self.admin.id
        self.assertEqual((await self._staff_decision("approve", self.principal)).status_code, 404)
        self.assertEqual((await self.client.patch("/api/schools/2", json={"action": "approve"})).status_code, 403)
        self.assertEqual(self.sent, [])

    async def test_unrelated_admin_actions_do_not_send_registration_decisions(self):
        response = await self._staff_decision("unlock")
        self.assertEqual(response.status_code, 200, response.text)
        response = await self._school_decision("deactivate")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.sent, [])


if __name__ == "__main__":
    unittest.main()
