"""Recovery authorization, ownership proof and account preservation in isolation."""
from __future__ import annotations

import asyncio
from contextvars import ContextVar
from datetime import date, datetime, timedelta, timezone
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import Depends, FastAPI, HTTPException
import httpx
from sqlalchemy import func, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateSchema, DropSchema

import app.models
from app.api import deps
from app.api.routers import email_corrections
from app.core.config import settings
from app.db.session import Base, get_db
from app.models.academic import GradeLevel, SchoolYear, Section, SectionAdviser, Subject, TeacherAssignment
from app.models.applications import ResubmissionChallenge
from app.models.email_corrections import EmailCorrectionChallenge, EmailCorrectionGuard
from app.models.schools import School
from app.models.staff import AuditLog, OTPCode, PasswordReset, Staff, StaffDevice, StaffSession
from app.models.students import Student, StudentEnrollment, StudentPace
from app.services import email_corrections as service
from app.services.email import EmailDeliveryError


TEST_POSTGRES_URL = os.environ.get("TRACE_TEST_POSTGRES_URL")
_REQUEST_SESSION = ContextVar("email_correction_test_session", default=None)


class EmailCorrectionTests(unittest.IsolatedAsyncioTestCase):
    postgres_url = None

    async def asyncSetUp(self):
        config = patch.multiple(settings, SECRET_KEY="isolated-email-correction-secret",
                                OTP_EXPIRY_MINUTES=5, OTP_MAX_ATTEMPTS=5,
                                DEBUG_OTP=True, LOG_OTP_CODES=True)
        config.start()
        self.addCleanup(config.stop)
        deps._BUCKETS.clear()
        self.schema = None
        if self.postgres_url:
            if make_url(self.postgres_url).host not in {"localhost", "127.0.0.1", "::1"}:
                self.fail("Recovery tests require an explicitly configured disposable loopback PostgreSQL database.")
            self.schema = "trace_email_correction_test_" + uuid4().hex
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
        self.people = {}
        async with self.sessions() as db:
            db.add_all([School(school_id=1, name="Own School", status="approved"),
                        School(school_id=2, name="Other School", status="approved"),
                        School(school_id=3, name="Principal School", status="approved")])
            await db.flush()
            for name, role, school_id, status in (
                ("Admin", "admin", 1, "approved"), ("Teacher", "teacher", 1, "approved"),
                ("Rejected", "teacher", 1, "rejected"), ("Colleague", "teacher", 1, "approved"),
                ("Other", "teacher", 2, "approved"), ("Principal", "admin", 3, "approved"),
                ("Operator", "super_admin", None, "approved"),
            ):
                person = Staff(id=uuid4(), school_id=school_id, email=f"{name.lower()}@example.com",
                               password_hash="isolated-test-hash", first_name=name, last_name="Person",
                               contact_number="09123456789", role=role, account_status=status,
                               is_active=True, is_approved=status == "approved",
                               rejection_reason="Original rejection" if status == "rejected" else None)
                db.add(person)
                self.people[name] = person
            await db.commit()
        self.actor = self.people["Admin"].id
        self.request_sessions = []

        async def isolated_db():
            async with self.sessions() as db:
                self.request_sessions.append(db)
                token = _REQUEST_SESSION.set(db)
                try:
                    yield db
                except Exception:
                    await db.rollback()
                    raise
                finally:
                    _REQUEST_SESSION.reset(token)

        async def actor(db=Depends(get_db)):
            if self.actor is None:
                raise HTTPException(401, "Sign in first.")
            return await db.get(Staff, self.actor)

        application = FastAPI()
        application.include_router(email_corrections.router)
        application.dependency_overrides[get_db] = isolated_db
        application.dependency_overrides[deps.get_current_user] = actor
        self.application = application
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://testserver")
        self.sent = []

        async def send(email, code, **kwargs):
            self.assertFalse(_REQUEST_SESSION.get().in_transaction())
            self.sent.append((email, code, kwargs))

        async def verify(password, password_hash):
            self.assertFalse(_REQUEST_SESSION.get().in_transaction())
            return password == "CorrectPassword1!"

        self.send = AsyncMock(side_effect=send)
        self.notice = AsyncMock()
        self.verify_password = AsyncMock(side_effect=verify)
        for name, replacement in (("send_email_correction_code", self.send),
                                  ("send_email_correction_notice", self.notice),
                                  ("async_verify_password", self.verify_password)):
            patcher = patch.object(service, name, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    async def asyncTearDown(self):
        await self.client.aclose()
        if self.schema:
            async with self.engine.begin() as connection:
                await connection.execute(DropSchema(self.schema, cascade=True))
        await self.engine.dispose()

    async def _request(self, name="Teacher", email="new.inbox@example.com", **changes):
        return await self.client.post(f"/api/staff/{self.people[name].id}/email-correction", json={
            "new_email": email, "current_password": "CorrectPassword1!",
            "reason": "Confirmed in person; old testing email is inaccessible.",
            "identity_confirmed": True, **changes,
        })

    async def _principal_request(self):
        return await self.client.post("/api/schools/3/principal-email-correction", json={
            "new_email": "principal.new@example.com", "current_password": "CorrectPassword1!",
            "reason": "Identity confirmed in person.", "identity_confirmed": True,
        })

    async def _verify(self, response, *, code=None, email=None):
        body = response.json()
        return await self.client.post("/api/auth/email-correction/verify", json={
            "challenge_id": body["challenge_id"], "new_email": email or body["new_email"],
            "code": self.sent[-1][1] if code is None else code,
        })

    async def _person(self, name="Teacher"):
        async with self.sessions() as db:
            return await db.get(Staff, self.people[name].id)

    async def _age_requests(self):
        async with self.sessions() as db:
            await db.execute(update(EmailCorrectionChallenge).values(created_at=service.now_utc() - timedelta(minutes=2)))
            await db.commit()

    async def test_request_sends_new_inbox_only_without_changing_account_or_exposing_code(self):
        response = await self._request(email="NEW.INBOX@EXAMPLE.COM")
        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(response.json()["expires_in"], 300)
        self.assertEqual(response.json()["new_email"], "new.inbox@example.com")
        self.assertNotIn("code", response.json())
        self.assertEqual((await self._person()).email, "teacher@example.com")
        self.assertEqual(self.sent[0][0], "new.inbox@example.com")
        async with self.sessions() as db:
            challenge = await db.get(EmailCorrectionChallenge, UUID(response.json()["challenge_id"]))
            self.assertNotEqual(challenge.code_digest, self.sent[0][1])
            self.assertEqual(len(challenge.code_digest), 64)
            self.assertIsNotNone(challenge.delivered_at)
            audits = (await db.scalars(select(AuditLog))).all()
            self.assertEqual([audit.action for audit in audits], ["email_correction_requested"])
            self.assertNotIn(self.sent[0][1], audits[0].detail)

    async def test_teacher_operator_and_cross_tenant_cannot_initiate_teacher_recovery(self):
        for name in ("Teacher", "Operator"):
            self.actor = self.people[name].id
            self.assertEqual((await self._request()).status_code, 403)
        self.actor = self.people["Admin"].id
        self.assertEqual((await self._request("Other")).status_code, 404)
        self.assertEqual((await self._request("Admin")).status_code, 403)
        self.assertEqual((await self._request("Principal")).status_code, 404)
        self.send.assert_not_awaited()

    async def test_unauthenticated_cannot_initiate(self):
        self.actor = None
        self.assertEqual((await self._request()).status_code, 401)

    async def test_identity_reason_and_password_are_required(self):
        for changes in ({"identity_confirmed": False}, {"reason": "  "},
                        {"reason": "x" * 501}, {"current_password": ""}):
            self.assertEqual((await self._request(**changes)).status_code, 422)
        self.send.assert_not_awaited()

    async def test_inactive_or_locked_principal_cannot_initiate_even_with_existing_session(self):
        for field, value in (("is_active", False), ("permanent_lock", True), ("requires_password_change", True)):
            async with self.sessions() as db:
                actor = await db.get(Staff, self.actor)
                setattr(actor, field, value)
                await db.commit()
            self.assertEqual((await self._request()).status_code, 403)
            async with self.sessions() as db:
                actor = await db.get(Staff, self.actor)
                setattr(actor, field, False if field != "is_active" else True)
                await db.commit()
        self.send.assert_not_awaited()

    async def test_wrong_admin_password_has_durable_limit_across_requests(self):
        for attempt in range(5):
            response = await self._request(current_password="WrongPassword")
            self.assertEqual(response.status_code, 403, response.text)
        response = await self._request()
        self.assertEqual(response.status_code, 429, response.text)
        self.assertGreater(int(response.headers["retry-after"]), 0)
        self.assertEqual(self.verify_password.await_count, 5)
        self.send.assert_not_awaited()
        async with self.sessions() as db:
            self.assertEqual(await db.scalar(select(func.count()).select_from(EmailCorrectionChallenge)), 0)
            guard = await db.get(EmailCorrectionGuard, self.actor)
            self.assertEqual(guard.password_failures, 5)
            guard.password_window_start = service.now_utc() - timedelta(minutes=16)
            await db.commit()
        self.assertEqual((await self._request()).status_code, 202)

    async def test_only_operator_can_correct_the_unique_principal(self):
        self.assertEqual((await self._principal_request()).status_code, 403)
        self.actor = self.people["Teacher"].id
        self.assertEqual((await self._principal_request()).status_code, 403)
        self.actor = self.people["Operator"].id
        response = await self._principal_request()
        self.assertEqual(response.status_code, 202, response.text)
        self.actor = None
        completed = await self._verify(response)
        self.assertEqual(completed.status_code, 200, completed.text)
        self.assertEqual((await self._person("Principal")).email, "principal.new@example.com")
        self.assertEqual((await self._person("Operator")).email, "operator@example.com")

    async def test_ambiguous_principal_rejected_without_sending(self):
        self.actor = self.people["Operator"].id
        async with self.sessions() as db:
            db.add(Staff(school_id=3, role="admin", email="second.principal@example.com",
                         password_hash="unused", first_name="Second", last_name="Principal",
                         contact_number="09123456789", account_status="approved", is_approved=True))
            await db.commit()
        self.assertEqual((await self._principal_request()).status_code, 409)
        self.send.assert_not_awaited()

    async def test_new_inbox_ownership_can_complete_without_old_sign_in(self):
        response = await self._request()
        self.actor = None
        completed = await self._verify(response)
        self.assertEqual(completed.status_code, 200, completed.text)
        self.assertEqual(completed.json()["email"], "new.inbox@example.com")
        self.assertEqual((await self._person()).email, "new.inbox@example.com")
        self.assertEqual(self.notice.await_count, 2)
        self.assertTrue(self.notice.await_args_list[0].kwargs["is_previous"])
        self.assertFalse(self.notice.await_args_list[1].kwargs["is_previous"])

    async def test_correction_preserves_account_approval_assignments_scores_and_revokes_credentials(self):
        person = self.people["Teacher"]
        now = service.now_utc()
        async with self.sessions() as db:
            db.add_all([SchoolYear(school_year_id=1, school_id=1, year="2026-2027", start_date=date(2026, 6, 1), end_date=date(2027, 4, 1)),
                        GradeLevel(grade_level_id=1, school_id=1, level="1", name="Grade 1")])
            await db.flush()
            db.add_all([Section(section_id=1, school_id=1, grade_level_id=1, name="Rose"),
                        Subject(subject_id=1, school_id=1, grade_level_id=1, subject_name="Math"),
                        Student(student_id=1, school_id=1, first_name="Learner", last_name="Person", created_by=person.id)])
            await db.flush()
            db.add_all([TeacherAssignment(assignment_id=1, school_id=1, school_year_id=1, section_id=1, subject_id=1, teacher_id=person.id),
                        SectionAdviser(adviser_id=1, school_id=1, school_year_id=1, section_id=1, teacher_id=person.id),
                        StudentEnrollment(enrollment_id=1, school_id=1, school_year_id=1, student_id=1, grade_level_id=1, section_id=1),
                        StaffDevice(id=uuid4(), staff_id=person.id, device_id="browser", is_trusted=True, last_2fa_verified=now)])
            await db.flush()
            device = await db.scalar(select(StaffDevice).where(StaffDevice.staff_id == person.id))
            db.add_all([StudentPace(pace_id=1, school_id=1, student_id=1, enrollment_id=1, subject_id=1, subject="Math", average_score=89, recorded_by=person.id),
                        StaffSession(staff_id=person.id, device_id=device.id, access_token="old-access", refresh_token="old-refresh", expires_at=now + timedelta(days=1)),
                        OTPCode(staff_id=person.id, code="123456", purpose="login", expires_at=now + timedelta(minutes=5)),
                        PasswordReset(staff_id=person.id, reset_token="old-reset-token", token_expires=now + timedelta(minutes=15)),
                        ResubmissionChallenge(staff_id=person.id, code_digest="a" * 64, state_digest="b" * 64, token_digest="c" * 64, expires_at=now + timedelta(minutes=5), token_expires_at=now + timedelta(minutes=15))])
            await db.commit()
        response = await self._request()
        completed = await self._verify(response)
        self.assertEqual(completed.status_code, 200, completed.text)
        updated = await self._person()
        self.assertEqual((updated.id, updated.password_hash, updated.role, updated.school_id, updated.account_status, updated.is_approved),
                         (person.id, "isolated-test-hash", "teacher", 1, "approved", True))
        async with self.sessions() as db:
            self.assertEqual((await db.get(TeacherAssignment, 1)).teacher_id, person.id)
            self.assertEqual((await db.get(SectionAdviser, 1)).teacher_id, person.id)
            self.assertEqual((await db.get(StudentPace, 1)).average_score, 89)
            self.assertEqual((await db.get(Student, 1)).created_by, person.id)
            self.assertFalse((await db.scalar(select(StaffSession))).is_active)
            self.assertFalse((await db.scalar(select(StaffDevice))).is_trusted)
            self.assertIsNone((await db.scalar(select(StaffDevice))).last_2fa_verified)
            self.assertTrue((await db.scalar(select(OTPCode))).is_used)
            self.assertIsNone((await db.scalar(select(PasswordReset))).reset_token)
            self.assertIsNone((await db.scalar(select(ResubmissionChallenge))).token_digest)
            self.assertIsNotNone((await db.scalar(select(ResubmissionChallenge))).used_at)
            self.assertEqual((await db.scalars(select(AuditLog.action).order_by(AuditLog.created_at))).all(),
                             ["email_correction_requested", "email_correction_completed"])

    async def test_rejected_account_keeps_its_decision_and_reason(self):
        response = await self._request("Rejected")
        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual((await self._verify(response)).status_code, 200)
        person = await self._person("Rejected")
        self.assertEqual(person.account_status, "rejected")
        self.assertFalse(person.is_approved)
        self.assertEqual(person.rejection_reason, "Original rejection")

    async def test_wrong_code_and_wrong_address_are_bounded_and_cannot_change_email(self):
        response = await self._request()
        correct = self.sent[-1][1]
        wrong = "000000" if correct != "000000" else "111111"
        self.assertEqual((await self._verify(response, email="different@example.com")).status_code, 400)
        for _ in range(3):
            self.assertEqual((await self._verify(response, code=wrong)).status_code, 400)
        self.assertEqual((await self._verify(response, code=wrong)).status_code, 410)
        self.assertEqual((await self._verify(response, code=correct)).status_code, 410)
        self.assertEqual((await self._person()).email, "teacher@example.com")

    async def test_expired_and_unknown_challenges_cannot_complete(self):
        response = await self._request()
        async with self.sessions() as db:
            await db.execute(update(EmailCorrectionChallenge).values(expires_at=service.now_utc() - timedelta(seconds=1)))
            await db.commit()
        self.assertEqual((await self._verify(response)).status_code, 410)
        unknown = await self.client.post("/api/auth/email-correction/verify", json={
            "challenge_id": str(uuid4()), "new_email": "new.inbox@example.com", "code": "123456",
        })
        self.assertEqual(unknown.status_code, 410)

    async def test_completed_code_is_single_use(self):
        response = await self._request()
        self.assertEqual((await self._verify(response)).status_code, 200)
        self.assertEqual((await self._verify(response)).status_code, 410)
        self.assertEqual(self.notice.await_count, 2)

    async def test_resend_has_durable_cooldown_and_replaces_old_code(self):
        first = await self._request()
        first_code = self.sent[-1][1]
        response = await self._request()
        self.assertEqual(response.status_code, 429, response.text)
        await self._age_requests()
        second = await self._request()
        self.assertEqual(second.status_code, 202, second.text)
        self.assertEqual((await self._verify(first, code=first_code)).status_code, 410)
        self.assertEqual((await self._verify(second)).status_code, 200)

    async def test_repeated_requests_are_bounded_per_target_and_successful_passwords_do_not_count_as_failures(self):
        for _ in range(5):
            self.assertEqual((await self._request()).status_code, 202)
            await self._age_requests()
        self.assertEqual((await self._request()).status_code, 429)
        async with self.sessions() as db:
            self.assertEqual((await db.get(EmailCorrectionGuard, self.actor)).password_failures, 0)

    async def test_delivery_failure_does_not_change_email_or_leave_usable_challenge(self):
        self.send.side_effect = EmailDeliveryError("unavailable")
        response = await self._request()
        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual((await self._person()).email, "teacher@example.com")
        async with self.sessions() as db:
            challenge = await db.scalar(select(EmailCorrectionChallenge))
            self.assertIsNotNone(challenge.used_at)
            self.assertIsNone(challenge.delivered_at)

    async def test_notification_failure_does_not_undo_verified_correction(self):
        response = await self._request()
        self.notice.side_effect = EmailDeliveryError("unavailable")
        self.assertEqual((await self._verify(response)).status_code, 200)
        self.assertEqual((await self._person()).email, "new.inbox@example.com")

    async def test_case_insensitive_duplicate_and_unchanged_email_are_rejected(self):
        response = await self._request(email="COLLEAGUE@EXAMPLE.COM")
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual((await self._request(email="TEACHER@EXAMPLE.COM")).status_code, 409)
        self.send.assert_not_awaited()

    async def test_address_claimed_after_delivery_is_not_stolen_at_verification(self):
        response = await self._request()
        async with self.sessions() as db:
            colleague = await db.get(Staff, self.people["Colleague"].id)
            colleague.email = "new.inbox@example.com"
            await db.commit()
        self.assertEqual((await self._verify(response)).status_code, 409)
        self.assertEqual((await self._person()).email, "teacher@example.com")

    async def test_actor_or_target_security_state_change_invalidates_existing_code(self):
        for name, field, value in (
            ("Admin", "role", "teacher"), ("Admin", "password_hash", "different-hash"),
            ("Admin", "is_active", False), ("Teacher", "password_hash", "different-hash"),
            ("Teacher", "role", "admin"), ("Teacher", "school_id", 2),
            ("Teacher", "account_status", "deactivated"),
        ):
            with self.subTest(name=name, field=field):
                response = await self._request()
                self.assertEqual(response.status_code, 202, response.text)
                async with self.sessions() as db:
                    person = await db.get(Staff, self.people[name].id)
                    old = getattr(person, field)
                    setattr(person, field, value)
                    await db.commit()
                self.assertEqual((await self._verify(response)).status_code, 410)
                async with self.sessions() as db:
                    person = await db.get(Staff, self.people[name].id)
                    setattr(person, field, old)
                    await db.commit()
                await self._age_requests()
                # Each scenario isolates stale-state validation from the hourly ceiling.
                async with self.sessions() as db:
                    await db.execute(update(EmailCorrectionChallenge).values(created_at=service.now_utc() - timedelta(hours=2)))
                    await db.commit()
        self.assertEqual((await self._person()).email, "teacher@example.com")

    async def test_school_suspension_invalidates_pending_code(self):
        response = await self._request()
        async with self.sessions() as db:
            school = await db.get(School, 1)
            school.status = "deactivated"
            await db.commit()
        self.assertEqual((await self._verify(response)).status_code, 410)

    async def test_password_change_during_delivery_invalidates_code(self):
        async def deliver_then_change(*args, **kwargs):
            async with self.sessions() as db:
                teacher = await db.get(Staff, self.people["Teacher"].id)
                teacher.password_hash = "changed-during-delivery"
                await db.commit()
        self.send.side_effect = deliver_then_change
        self.assertEqual((await self._request()).status_code, 410)
        self.assertEqual((await self._person()).email, "teacher@example.com")


@unittest.skipUnless(TEST_POSTGRES_URL, "Set TRACE_TEST_POSTGRES_URL for isolated loopback PostgreSQL recovery races.")
class EmailCorrectionPostgresTests(EmailCorrectionTests):
    postgres_url = TEST_POSTGRES_URL

    async def test_concurrent_verifications_consume_challenge_only_once(self):
        response = await self._request()
        code = self.sent[-1][1]
        first, second = await asyncio.wait_for(asyncio.gather(
            self._verify(response, code=code), self._verify(response, code=code),
        ), timeout=15)
        self.assertEqual(sorted([first.status_code, second.status_code]), [200, 410],
                         [first.text, second.text])
        self.assertEqual(self.notice.await_count, 2)
        async with self.sessions() as db:
            self.assertEqual(await db.scalar(select(func.count()).select_from(AuditLog).where(
                AuditLog.action == "email_correction_completed",
            )), 1)

    async def test_concurrent_corrections_cannot_claim_same_email_for_different_accounts(self):
        first = await self._request("Teacher", email="shared.inbox@example.com")
        first_code = self.sent[-1][1]
        second = await self._request("Colleague", email="shared.inbox@example.com")
        second_code = self.sent[-1][1]
        responses = await asyncio.wait_for(asyncio.gather(
            self._verify(first, code=first_code), self._verify(second, code=second_code),
        ), timeout=15)
        self.assertEqual(sorted(response.status_code for response in responses), [200, 409],
                         [response.text for response in responses])
        async with self.sessions() as db:
            self.assertEqual(await db.scalar(select(func.count()).select_from(Staff).where(
                func.lower(Staff.email) == "shared.inbox@example.com",
            )), 1)

    async def test_parallel_password_checks_have_consistent_guard_lock_order(self):
        responses = await asyncio.wait_for(asyncio.gather(
            self._request("Teacher"), self._request("Colleague", email="another.inbox@example.com"),
        ), timeout=15)
        self.assertEqual([response.status_code for response in responses], [202, 202],
                         [response.text for response in responses])
        async with self.sessions() as db:
            self.assertEqual((await db.get(EmailCorrectionGuard, self.actor)).password_failures, 0)

    async def test_concurrent_registration_and_recovery_share_canonical_email_lock(self):
        from app.api.routers import auth
        from registration_email_helpers import issue_registration_proof

        self.application.include_router(auth.router)
        email = "shared.registration@example.com"
        correction = await self._request(email=email)
        correction_code = self.sent[-1][1]
        token = await issue_registration_proof(self.sessions, email)
        with patch.object(auth, "async_get_password_hash", AsyncMock(return_value="synthetic-registration-hash")):
            corrected, registered = await asyncio.wait_for(asyncio.gather(
                self._verify(correction, code=correction_code),
                self.client.post("/api/auth/register", json={
                    "school_id": 1, "email": email.upper(), "password": "NewApplicantPassword1!",
                    "first_name": "New", "last_name": "Applicant", "contact_number": "09123456789",
                    "email_verification_token": token,
                }),
            ), timeout=15)
        self.assertIn(corrected.status_code, (200, 409), corrected.text)
        self.assertIn(registered.status_code, (201, 409), registered.text)
        self.assertNotEqual(corrected.status_code == 200, registered.status_code == 201,
                            [corrected.text, registered.text])
        async with self.sessions() as db:
            self.assertEqual(await db.scalar(select(func.count()).select_from(Staff).where(
                func.lower(Staff.email) == email,
            )), 1)


if __name__ == "__main__":
    unittest.main()
