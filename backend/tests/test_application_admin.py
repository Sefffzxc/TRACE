"""Application decisions, durable history and school boundaries in an isolated DB."""
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

from fastapi import Depends, FastAPI, HTTPException
import httpx
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models
from app.api import deps
from app.api.routers import schools, staff
from app.db.session import Base, get_db
from app.models.applications import ApplicationEvent, ResubmissionChallenge
from app.models.schools import School
from app.models.staff import Staff
from app.services.email import EmailDeliveryError


class ApplicationAdminTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False, autoflush=False)
        async with self.engine.begin() as connection:
            await connection.execute(text("PRAGMA foreign_keys=ON"))
            await connection.run_sync(Base.metadata.create_all)
        self.people = {}
        async with self.sessions() as db:
            db.add_all([
                School(school_id=1, name="Our School", status="approved"),
                School(school_id=2, name="Other School", status="approved"),
                School(school_id=3, name="Applicant School", status="pending"),
            ])
            await db.flush()
            for name, role, school_id, account_status in (
                ("Admin", "admin", 1, "approved"),
                ("Colleague", "admin", 1, "approved"),
                ("Teacher", "teacher", 1, "pending"),
                ("Rejected", "teacher", 1, "rejected"),
                ("Blocked", "teacher", 1, "blocked"),
                ("Other", "teacher", 2, "rejected"),
                ("Principal", "admin", 3, "pending"),
                ("Operator", "super_admin", None, "approved"),
            ):
                identity = uuid4()
                person = Staff(
                    id=identity, school_id=school_id, role=role,
                    email=f"{identity}@example.com", password_hash="unused-test-hash",
                    first_name=name, last_name="Applicant", contact_number="09123456789",
                    account_status=account_status, is_approved=account_status == "approved",
                    is_active=account_status != "blocked",
                    rejection_reason="Original reason." if account_status in {"rejected", "blocked"} else None,
                )
                db.add(person)
                self.people[name] = person
            await db.commit()
        self.actor = self.people["Admin"].id

        async def isolated_db():
            async with self.sessions() as db:
                try:
                    yield db
                except Exception:
                    await db.rollback()
                    raise

        async def current_actor(db=Depends(get_db)):
            return await db.get(Staff, self.actor)

        application = FastAPI()
        application.include_router(staff.router)
        application.include_router(schools.router)
        application.dependency_overrides[get_db] = isolated_db
        application.dependency_overrides[deps.get_current_user] = current_actor
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://testserver")
        self.staff_mail = AsyncMock()
        self.school_mail = AsyncMock()
        for module, mock in ((staff, self.staff_mail), (schools, self.school_mail)):
            replacement = patch.object(module, "send_account_decision_email", mock)
            replacement.start()
            self.addCleanup(replacement.stop)

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.engine.dispose()

    async def _act(self, name, action, reason="Please correct the details.", **extra):
        return await self.client.patch(f"/api/staff/{self.people[name].id}", json={
            "action": action, "reason": reason, **extra,
        })

    async def _school_act(self, action, reason="Please correct the school details."):
        self.actor = self.people["Operator"].id
        return await self.client.patch("/api/schools/3", json={"action": action, "reason": reason})

    async def _history(self, name):
        return await self.client.get(f"/api/staff/{self.people[name].id}/application-history")

    async def _person(self, name):
        async with self.sessions() as db:
            return await db.get(Staff, self.people[name].id)

    async def test_application_queues_are_scoped_teacher_only_and_validate_status(self):
        for wanted, expected in (("pending", "Teacher"), ("rejected", "Rejected"), ("blocked", "Blocked")):
            response = await self.client.get("/api/staff/applications", params={"status": wanted})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual([row["first_name"] for row in response.json()], [expected])
        response = await self.client.get("/api/staff/applications?status=approved")
        self.assertEqual(response.status_code, 422)
        self.actor = self.people["Teacher"].id
        self.assertEqual((await self.client.get("/api/staff/applications")).status_code, 403)

    async def test_reject_and_block_require_nonblank_reason_for_both_application_types(self):
        for action in ("reject", "block"):
            for reason in (None, "", "  \n\t "):
                with self.subTest(action=action, reason=reason):
                    self.assertEqual((await self._act("Teacher", action, reason)).status_code, 422)
                    self.assertEqual((await self._school_act(action, reason)).status_code, 422)
                    self.actor = self.people["Admin"].id
        self.staff_mail.assert_not_awaited()
        self.school_mail.assert_not_awaited()

    async def test_reject_reopen_and_approve_preserve_reason_history_and_same_account(self):
        identity = self.people["Teacher"].id
        response = await self._act("Teacher", "reject", "  Wrong employee number.  ")
        self.assertEqual(response.status_code, 200, response.text)
        response = await self._act("Teacher", "reopen")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["user"]["account_status"], "pending")
        self.assertFalse(response.json()["user"]["is_approved"])
        self.assertEqual((await self._act("Teacher", "approve")).status_code, 200)
        person = await self._person("Teacher")
        self.assertEqual(person.id, identity)
        self.assertEqual(person.account_status, "approved")
        history = await self._history("Teacher")
        self.assertEqual(history.status_code, 200, history.text)
        events = history.json()
        self.assertEqual([event["action"] for event in events], ["approve", "reopen", "reject"])
        self.assertEqual(events[-1]["reason"], "Wrong employee number.")
        self.assertTrue(all(event["actor_name"] == "Admin Applicant" for event in events))
        self.assertEqual([call.kwargs["decision"] for call in self.staff_mail.await_args_list], ["rejected", "reopened", "approved"])

    async def test_legacy_rejection_is_preserved_before_reopen_or_direct_approval(self):
        response = await self._act("Rejected", "approve")
        self.assertEqual(response.status_code, 200, response.text)
        events = (await self._history("Rejected")).json()
        self.assertEqual([event["action"] for event in events], ["approve", "reject"])
        self.assertEqual(events[-1]["reason"], "Original reason.")

    async def test_legacy_history_read_is_persisted_once(self):
        first = (await self._history("Rejected")).json()
        second = (await self._history("Rejected")).json()
        self.assertEqual(first, second)
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0]["to_status"], "rejected")

    async def test_blocked_application_requires_explicit_reopen_even_for_admin(self):
        for action in ("approve", "reactivate", "reject", "deactivate"):
            response = await self._act("Blocked", action)
            self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual((await self._act("Blocked", "unlock")).status_code, 200)
        self.assertEqual((await self._person("Blocked")).account_status, "blocked")
        response = await self._act("Blocked", "reopen")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["user"]["account_status"], "pending")
        self.assertFalse(response.json()["user"]["is_approved"])
        self.assertEqual((await self._act("Blocked", "approve")).status_code, 200)

    async def test_block_sets_denial_and_disappears_from_ordinary_directories(self):
        self.assertEqual((await self._act("Teacher", "approve")).status_code, 200)
        response = await self._act("Teacher", "block", "Repeated abusive applications.")
        self.assertEqual(response.status_code, 200, response.text)
        person = await self._person("Teacher")
        self.assertFalse(person.is_active)
        self.assertFalse(person.is_approved)
        for path in ("/api/staff", "/api/staff/teachers"):
            response = await self.client.get(path)
            self.assertNotIn(str(person.id), {row["id"] for row in response.json()})
        async with self.sessions() as db:
            with self.assertRaises(HTTPException) as caught:
                await deps.require_account_access(person, db)
        self.assertEqual(caught.exception.status_code, 403)
        self.assertIn("blocked", caught.exception.detail)

    async def test_admin_cannot_promote_teacher_manage_principal_or_block_self(self):
        response = await self._act("Teacher", "approve", role="admin")
        self.assertEqual(response.status_code, 403, response.text)
        for action in ("approve", "reject", "block", "reopen", "force_reset"):
            response = await self._act("Colleague", action)
            self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual((await self._act("Admin", "block")).status_code, 400)
        self.staff_mail.assert_not_awaited()

    async def test_school_admin_cannot_delete_another_principal(self):
        response = await self.client.delete(f"/api/staff/{self.people['Colleague'].id}")
        self.assertEqual(response.status_code, 403, response.text)
        self.assertIsNotNone(await self._person("Colleague"))

    async def test_cross_school_decisions_and_history_are_not_discoverable(self):
        self.assertEqual((await self._act("Other", "approve")).status_code, 404)
        self.assertEqual((await self._history("Other")).status_code, 404)
        self.assertEqual((await self.client.get("/api/schools/3/application-history")).status_code, 403)

    async def test_teachers_cannot_read_other_applicants_private_decisions(self):
        self.actor = self.people["Teacher"].id
        for name, wanted in (("Rejected", "rejected"), ("Blocked", "blocked")):
            response = await self.client.get("/api/staff", params={"account_status": wanted})
            self.assertEqual(response.status_code, 403, response.text)
            response = await self.client.get(f"/api/staff/{self.people[name].id}")
            self.assertEqual(response.status_code, 404, response.text)
            self.assertEqual((await self._history(name)).status_code, 403)

    async def test_prior_approval_marker_survives_rejection_reopen_and_reapproval(self):
        earlier = datetime.now(timezone.utc) - timedelta(days=20)
        async with self.sessions() as db:
            person = await db.get(Staff, self.people["Teacher"].id)
            person.approved_at = earlier
            await db.commit()
        for action in ("reject", "reopen", "approve"):
            self.assertEqual((await self._act("Teacher", action)).status_code, 200)
            person = await self._person("Teacher")
            self.assertEqual(person.approved_at.replace(tzinfo=timezone.utc), earlier)

    async def test_decisions_invalidate_active_email_correction_challenges(self):
        async with self.sessions() as db:
            challenge = ResubmissionChallenge(
                staff_id=self.people["Rejected"].id, code_digest="a" * 64,
                state_digest="b" * 64, expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
            )
            db.add(challenge)
            await db.commit()
            identity = challenge.id
        self.assertEqual((await self._act("Rejected", "reopen")).status_code, 200)
        async with self.sessions() as db:
            self.assertIsNotNone((await db.get(ResubmissionChallenge, identity)).used_at)

    async def test_school_rejection_reopen_and_direct_approval_keep_principal_consistent(self):
        for action, wanted in (("reject", "rejected"), ("reopen", "pending"), ("approve", "approved")):
            response = await self._school_act(action)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["status"], wanted)
            person = await self._person("Principal")
            self.assertEqual(person.account_status, wanted)
            self.assertEqual(person.is_approved, wanted == "approved")
        response = await self.client.get("/api/schools/3/application-history")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual([row["action"] for row in response.json()], ["approve", "reopen", "reject"])
        self.assertEqual([call.kwargs["decision"] for call in self.school_mail.await_args_list], ["rejected", "reopened", "approved"])

    async def test_school_block_requires_reopen_and_restores_only_pending(self):
        response = await self._school_act("block")
        self.assertEqual(response.status_code, 200, response.text)
        for action in ("approve", "reactivate", "reject", "deactivate"):
            response = await self._school_act(action)
            self.assertEqual(response.status_code, 409, response.text)
        response = await self._school_act("reopen")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "pending")
        person = await self._person("Principal")
        self.assertTrue(person.is_active)
        self.assertFalse(person.is_approved)
        self.assertEqual(person.account_status, "pending")

    async def test_history_and_application_state_roll_back_when_new_decision_email_fails(self):
        self.staff_mail.side_effect = EmailDeliveryError("simulated email outage")
        for action in ("reject", "block"):
            response = await self._act("Teacher", action)
            self.assertEqual(response.status_code, 503, response.text)
            self.assertEqual((await self._person("Teacher")).account_status, "pending")
        async with self.sessions() as db:
            count = await db.scalar(select(func.count()).select_from(ApplicationEvent))
        self.assertEqual(count, 0)
        self.school_mail.side_effect = EmailDeliveryError("simulated email outage")
        for action in ("reject", "block"):
            response = await self._school_act(action)
            self.assertEqual(response.status_code, 503, response.text)
            self.assertEqual((await self._person("Principal")).account_status, "pending")
            async with self.sessions() as db:
                school = await db.get(School, 3)
                self.assertEqual(school.status, "pending")
                count = await db.scalar(select(func.count()).select_from(ApplicationEvent))
                self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()
