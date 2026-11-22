"""Rejected applicants prove email ownership before limited, single-use corrections.

All databases and mail/CAPTCHA providers are isolated test doubles. No live
application database, Cloudflare request or email is used.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs
from uuid import UUID, uuid4

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bcrypt
from fastapi import FastAPI
import httpx
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models
from app.api import deps
from app.api.routers import auth, resubmission, schools, staff
from app.core.config import settings
from app.core.security import create_access_token, create_refresh_token
from app.db.session import Base, get_db
from app.models.academic import GradeLevel, Section, SectionAdviser, TeacherAssignment
from app.models.applications import ApplicationEvent, ResubmissionChallenge
from app.models.schools import School
from app.models.staff import OTPCode, Staff, StaffDevice, StaffSession
from app.services import captcha
from app.services.applications import aware, correction_token_digest, verification_digest
from app.services.email import EmailDeliveryError


HTTPClient = httpx.AsyncClient
PASSWORD = "RejectedAccountPassword1!"
REASON = "Please correct the employee number."
SECRET = "private-resubmission-test-captcha-key"


class ResubmissionTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.password_hash = bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt(rounds=4)).decode()

    async def asyncSetUp(self):
        configuration = patch.multiple(
            settings, CAPTCHA_ENABLED=False, EMAIL_ENABLED=False,
            SECRET_KEY="resubmission-isolated-test-signing-key", DEBUG_OTP=False,
            LOG_OTP_CODES=False, OTP_EXPIRY_MINUTES=5, OTP_MAX_ATTEMPTS=5,
        )
        configuration.start()
        self.addCleanup(configuration.stop)
        deps._BUCKETS.clear()
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False, autoflush=False)
        async with self.engine.begin() as connection:
            await connection.execute(text("PRAGMA foreign_keys=ON"))
            await connection.run_sync(Base.metadata.create_all)
        self.people = {}
        async with self.sessions() as db:
            db.add_all([
                School(school_id=1, name="Original Test School", city="Cagayan de Oro", status="approved"),
                School(school_id=2, name="Destination Test School", city="Iligan", status="approved"),
                School(school_id=3, name="Rejected Test School", city="Butuan", status="rejected", rejection_reason=REASON),
                School(school_id=4, name="Pending Test School", city="Davao", status="pending"),
            ])
            await db.flush()
            for name, role, school_id, account_status, active in (
                ("Teacher", "teacher", 1, "rejected", True),
                ("Second", "teacher", 1, "rejected", True),
                ("Principal", "admin", 3, "rejected", True),
                ("Approved", "teacher", 1, "approved", True),
                ("Blocked", "teacher", 1, "blocked", False),
                ("Inactive", "teacher", 1, "rejected", False),
                ("Pending", "teacher", 1, "pending", True),
                ("Inconsistent", "admin", 4, "rejected", True),
                ("Admin", "admin", 1, "approved", True),
                ("DestinationAdmin", "admin", 2, "approved", True),
                ("Operator", "super_admin", None, "approved", True),
            ):
                person = Staff(
                    id=uuid4(), school_id=school_id, role=role,
                    email=f"{name.lower()}@example.com", password_hash=self.password_hash,
                    first_name=name, last_name="Applicant", contact_number="09123456789",
                    account_status=account_status, is_active=active,
                    is_approved=account_status == "approved",
                    rejection_reason=REASON if account_status == "rejected" else None,
                )
                db.add(person)
                self.people[name] = person
            await db.commit()
        self.request_sessions = []

        async def isolated_db():
            async with self.sessions() as db:
                self.request_sessions.append(db)
                try:
                    yield db
                except Exception:
                    await db.rollback()
                    raise

        async def no_throttle():
            # General rate limits have dedicated coverage. Exercise challenge
            # attempt limits without letting HTTP 429 mask their behavior.
            return None

        application = FastAPI()
        for router in (resubmission.router, auth.router, staff.router, schools.router):
            application.include_router(router)
        application.dependency_overrides[get_db] = isolated_db
        for limit in (deps.limit_resubmission_request, deps.limit_resubmission_verify, deps.limit_resubmission_submit):
            application.dependency_overrides[limit] = no_throttle
        self.client = HTTPClient(transport=httpx.ASGITransport(app=application), base_url="http://testserver")
        self.sent = []

        async def send_email(email, code, *, purpose):
            self.assertEqual(purpose, "resubmission")
            self.assertFalse(self.request_sessions[-1].in_transaction(), "Email delivery must release the DB transaction first.")
            self.sent.append((email, code))

        self.send_email = AsyncMock(side_effect=send_email)
        replacement = patch.object(resubmission, "send_otp_email", self.send_email)
        replacement.start()
        self.addCleanup(replacement.stop)
        for module in (staff, schools):
            replacement = patch.object(module, "send_account_decision_email", AsyncMock())
            replacement.start()
            self.addCleanup(replacement.stop)

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.engine.dispose()
        deps._BUCKETS.clear()

    async def _request(self, name="Teacher", **extra):
        return await self.client.post("/api/auth/resubmission/request", json={
            "email": self.people[name].email, **extra,
        })

    async def _issue(self, name="Teacher"):
        response = await self._request(name)
        self.assertEqual(response.status_code, 202, response.text)
        email, code = self.sent[-1]
        self.assertEqual(email, self.people[name].email)
        return response.json()["challenge_id"], code

    async def _verify(self, challenge_id, code, name="Teacher"):
        return await self.client.post("/api/auth/resubmission/verify", json={
            "email": self.people[name].email, "challenge_id": str(challenge_id), "code": code,
        })

    async def _proof(self, name="Teacher"):
        challenge_id, code = await self._issue(name)
        response = await self._verify(challenge_id, code, name)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["resubmission_token"], response.json()["application"]

    async def _submit(self, token, **extra):
        return await self.client.post("/api/auth/resubmission/submit", json={
            "token": token, "first_name": "Corrected", "last_name": "Applicant",
            "contact_number": "09987654321", "employee_no": "EMP-CORRECTED", **extra,
        })

    def _headers(self, name):
        return {"Authorization": f"Bearer {create_access_token({'sub': str(self.people[name].id)})}"}

    async def _count(self, model):
        async with self.sessions() as db:
            return await db.scalar(select(func.count()).select_from(model))

    async def test_request_response_is_generic_for_unknown_and_ineligible_addresses(self):
        reference = None
        for name in (None, "Approved", "Blocked", "Inactive", "Pending", "Inconsistent"):
            email = self.people[name].email if name else "unknown@example.com"
            response = await self.client.post("/api/auth/resubmission/request", json={"email": email})
            self.assertEqual(response.status_code, 202, response.text)
            payload = response.json()
            UUID(payload["challenge_id"])
            self.assertEqual(set(payload), {"message", "challenge_id"})
            reference = reference or payload["message"]
            self.assertEqual(payload["message"], reference)
            self.assertEqual((await self._verify(payload["challenge_id"], "123456")).status_code, 400)
        self.send_email.assert_not_awaited()
        self.assertEqual(await self._count(ResubmissionChallenge), 0)

    async def test_request_only_emails_eligible_teacher_or_principal_without_leaking_code(self):
        for name in ("Teacher", "Principal"):
            response = await self._request(name)
            self.assertEqual(response.status_code, 202, response.text)
            _, code = self.sent[-1]
            self.assertNotIn(code, response.text)
            self.assertNotIn("debug_otp", response.text)
            self.assertNotIn("password_hash", response.text)
        self.assertEqual([email for email, _ in self.sent], [self.people["Teacher"].email, self.people["Principal"].email])

    async def test_code_is_hashed_purpose_scoped_and_expires_after_five_minutes(self):
        before = datetime.now(timezone.utc)
        challenge_id, code = await self._issue()
        async with self.sessions() as db:
            challenge = await db.get(ResubmissionChallenge, UUID(challenge_id))
            self.assertNotEqual(challenge.code_digest, code)
            self.assertEqual(challenge.code_digest, verification_digest(challenge.id, code))
            self.assertLessEqual(abs((aware(challenge.expires_at) - before).total_seconds() - 300), 5)
            self.assertEqual(challenge.attempts, 0)
        self.assertEqual(await self._count(OTPCode), 0, "Correction codes must never become login OTPs.")

    async def test_email_failure_is_generic_and_creates_no_usable_challenge(self):
        self.send_email.side_effect = EmailDeliveryError("simulated provider outage")
        with self.assertNoLogs("app", level="WARNING"):
            response = await self._request()
        self.assertEqual(response.status_code, 202, response.text)
        self.assertNotIn("provider", response.text)
        self.assertEqual(await self._count(ResubmissionChallenge), 0)
        response = await self._verify(response.json()["challenge_id"], "123456")
        self.assertEqual(response.status_code, 400)

    async def test_captcha_missing_or_wrong_action_fails_before_email_and_valid_action_works(self):
        provider_calls = []

        def handle(request):
            fields = parse_qs(request.content.decode())
            provider_calls.append(fields)
            action = "resubmit" if fields["response"] == ["valid-resubmit-token"] else "register_teacher"
            return httpx.Response(200, json={"success": True, "hostname": "traceedu.app", "action": action})

        with patch.multiple(settings, CAPTCHA_ENABLED=True, TURNSTILE_SITE_KEY="public-test-site-key",
                            TURNSTILE_SECRET_KEY=SECRET, TURNSTILE_ALLOWED_HOSTNAMES="traceedu.app"):
            with patch.object(captcha.httpx, "AsyncClient", side_effect=lambda **kwargs: HTTPClient(transport=httpx.MockTransport(handle))):
                response = await self._request()
                self.assertEqual(response.status_code, 400, response.text)
                self.assertEqual(response.json()["detail"]["code"], "captcha_required")
                self.send_email.assert_not_awaited()
                response = await self._request(captcha_token="valid-but-wrong-action")
                self.assertEqual(response.status_code, 400, response.text)
                self.send_email.assert_not_awaited()
                response = await self._request(captcha_token="valid-resubmit-token")
                self.assertEqual(response.status_code, 202, response.text)
        self.send_email.assert_awaited_once()
        self.assertEqual(len(provider_calls), 2)
        self.assertNotIn(SECRET, response.text)

    async def test_wrong_email_or_missing_challenge_does_not_disclose_or_consume_it(self):
        challenge_id, code = await self._issue()
        self.assertEqual((await self._verify(challenge_id, code, "Second")).status_code, 400)
        self.assertEqual((await self._verify(uuid4(), code)).status_code, 400)
        self.assertEqual((await self._verify(challenge_id, code)).status_code, 200)

    async def test_five_bad_codes_lock_challenge_and_resend_invalidates_previous_code(self):
        challenge_id, code = await self._issue()
        wrong = "111111" if code != "111111" else "222222"
        for _ in range(5):
            self.assertEqual((await self._verify(challenge_id, wrong)).status_code, 400)
        self.assertEqual((await self._verify(challenge_id, code)).status_code, 400)
        async with self.sessions() as db:
            self.assertEqual((await db.get(ResubmissionChallenge, UUID(challenge_id))).attempts, 5)
        new_challenge, new_code = await self._issue()
        self.assertNotEqual(new_challenge, challenge_id)
        self.assertEqual((await self._verify(challenge_id, code)).status_code, 400)
        self.assertEqual((await self._verify(new_challenge, new_code)).status_code, 200)

    async def test_resend_invalidates_an_unverified_older_code(self):
        old_challenge, old_code = await self._issue()
        new_challenge, new_code = await self._issue()
        self.assertEqual((await self._verify(old_challenge, old_code)).status_code, 400)
        self.assertEqual((await self._verify(new_challenge, new_code)).status_code, 200)

    async def test_expired_code_does_not_issue_a_token(self):
        challenge_id, code = await self._issue()
        async with self.sessions() as db:
            challenge = await db.get(ResubmissionChallenge, UUID(challenge_id))
            challenge.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            await db.commit()
        self.assertEqual((await self._verify(challenge_id, code)).status_code, 400)

    async def test_verified_code_is_single_use_and_token_is_hashed_restricted_and_fifteen_minutes(self):
        challenge_id, code = await self._issue()
        before = datetime.now(timezone.utc)
        response = await self._verify(challenge_id, code)
        self.assertEqual(response.status_code, 200, response.text)
        token = response.json()["resubmission_token"]
        self.assertGreaterEqual(len(token), 32)
        self.assertNotIn(".", token)
        self.assertEqual(response.json()["expires_in"], 900)
        self.assertNotIn("access_token", response.json())
        self.assertNotIn("password_hash", json.dumps(response.json()))
        async with self.sessions() as db:
            challenge = await db.get(ResubmissionChallenge, UUID(challenge_id))
            self.assertEqual(challenge.token_digest, correction_token_digest(token))
            self.assertNotEqual(challenge.token_digest, token)
            self.assertLessEqual(abs((aware(challenge.token_expires_at) - before).total_seconds() - 900), 5)
        self.assertEqual((await self._verify(challenge_id, code)).status_code, 400)
        response = await self.client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(response.status_code, 401, response.text)
        self.assertEqual(await self._count(StaffSession), 0)
        self.assertEqual(await self._count(StaffDevice), 0)

    async def test_submission_requires_proof_and_cannot_change_email_role_or_password(self):
        self.assertEqual((await self._submit("a" * 40)).status_code, 401)
        token, _ = await self._proof()
        for forbidden in ({"email": "replacement@example.com"}, {"role": "admin"}, {"password": "ChangedPassword1!"}, {"password_hash": "replacement"}):
            response = await self._submit(token, **forbidden)
            self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual((await self._submit(token)).status_code, 200)

    async def test_resubmit_preserves_identity_reason_history_and_returns_to_pending_without_login(self):
        identity, original_email = self.people["Teacher"].id, self.people["Teacher"].email
        token, application = await self._proof()
        self.assertEqual(application["rejection_reason"], REASON)
        self.assertTrue(application["can_change_school"])
        response = await self._submit(token)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["account_status"], "pending")
        async with self.sessions() as db:
            person = await db.get(Staff, identity)
            self.assertEqual(person.email, original_email)
            self.assertEqual(person.password_hash, self.password_hash)
            self.assertEqual(person.role, "teacher")
            self.assertEqual(person.first_name, "Corrected")
            self.assertEqual(person.account_status, "pending")
            self.assertFalse(person.is_approved)
            self.assertIsNone(person.rejection_reason)
            events = (await db.scalars(select(ApplicationEvent).where(ApplicationEvent.staff_id == identity))).all()
            self.assertTrue(any(event.to_status == "rejected" and event.reason == REASON for event in events))
            self.assertTrue(any(event.action == "resubmitted" and event.to_status == "pending" for event in events))
        response = await self.client.post("/api/auth/login", json={
            "email": original_email, "password": PASSWORD, "device_id": "resubmission-test-device",
        })
        self.assertEqual(response.status_code, 403, response.text)
        self.assertIn("awaiting", response.text)
        self.assertEqual(await self._count(StaffSession), 0)
        self.assertEqual((await self._submit(token)).status_code, 401)

    async def test_expired_correction_token_cannot_submit(self):
        token, _ = await self._proof()
        async with self.sessions() as db:
            challenge = await db.scalar(select(ResubmissionChallenge).where(ResubmissionChallenge.token_digest == correction_token_digest(token)))
            challenge.token_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            await db.commit()
        self.assertEqual((await self._submit(token)).status_code, 401)

    async def test_same_school_correction_invalidates_old_login_codes_devices_and_sessions(self):
        async with self.sessions() as db:
            identity = self.people["Teacher"].id
            device = StaffDevice(staff_id=identity, device_id="existing-browser", is_trusted=True)
            db.add(device)
            await db.flush()
            session = StaffSession(
                staff_id=identity, device_id=device.id, access_token="old-access-token",
                refresh_token="old-refresh-token", expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            )
            otp = OTPCode(staff_id=identity, code="735826", purpose="login",
                          expires_at=datetime.now(timezone.utc) + timedelta(minutes=5))
            db.add_all([session, otp])
            await db.commit()
            device_id, session_id, otp_id = device.id, session.id, otp.id
        token, application = await self._proof()
        self.assertFalse(application["can_change_school"])
        response = await self._submit(token)
        self.assertEqual(response.status_code, 200, response.text)
        async with self.sessions() as db:
            self.assertFalse((await db.get(StaffDevice, device_id)).is_trusted)
            self.assertFalse((await db.get(StaffSession, session_id)).is_active)
            self.assertTrue((await db.get(OTPCode, otp_id)).is_used)

    async def test_pre_correction_access_and_refresh_tokens_stay_revoked_after_reapproval(self):
        identity = self.people["Teacher"].id
        old_access = create_access_token({"sub": str(identity), "jti": str(uuid4())})
        old_refresh = create_refresh_token({"sub": str(identity), "jti": str(uuid4())})
        async with self.sessions() as db:
            device = StaffDevice(staff_id=identity, device_id="old-approved-browser", is_trusted=True)
            db.add(device)
            await db.flush()
            db.add(StaffSession(staff_id=identity, device_id=device.id,
                access_token=old_access, refresh_token=old_refresh,
                expires_at=datetime.now(timezone.utc) + timedelta(hours=1)))
            await db.commit()
        token, _ = await self._proof()
        self.assertEqual((await self._submit(token)).status_code, 200)
        response = await self.client.patch(f"/api/staff/{identity}", headers=self._headers("Admin"), json={"action": "approve"})
        self.assertEqual(response.status_code, 200, response.text)
        for method, path, old_token in (("GET", "/api/auth/me", old_access), ("POST", "/api/auth/refresh", old_refresh)):
            response = await self.client.request(method, path, headers={"Authorization": f"Bearer {old_token}"})
            self.assertEqual(response.status_code, 401, response.text)
        current = create_access_token({"sub": str(identity), "jti": str(uuid4())})
        response = await self.client.get("/api/auth/me", headers={"Authorization": f"Bearer {current}"})
        self.assertEqual(response.status_code, 200, response.text)

    async def test_approved_blocked_or_new_rejection_between_request_and_verification_invalidates_code(self):
        for state in ("approved", "blocked", "rejected"):
            async with self.sessions() as db:
                person = await db.get(Staff, self.people["Teacher"].id)
                person.account_status = "rejected"
                person.is_active = True
                person.rejection_reason = REASON
                await db.commit()
            challenge_id, code = await self._issue()
            async with self.sessions() as db:
                person = await db.get(Staff, self.people["Teacher"].id)
                person.account_status = state
                person.rejection_reason = f"Different decision: {state}"
                await db.commit()
            self.assertEqual((await self._verify(challenge_id, code)).status_code, 400)

    async def test_admin_approval_block_or_new_rejection_invalidates_previously_verified_token(self):
        for action in ("approve", "block", "reject"):
            async with self.sessions() as db:
                person = await db.get(Staff, self.people["Teacher"].id)
                person.account_status = "rejected"
                person.is_active = True
                person.is_approved = False
                person.rejection_reason = REASON
                await db.commit()
            token, _ = await self._proof()
            response = await self.client.patch(f"/api/staff/{self.people['Teacher'].id}",
                headers=self._headers("Admin"), json={"action": action, "reason": "An updated administrative decision."})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual((await self._submit(token)).status_code, 401)

    async def test_change_school_requires_confirmation_then_keeps_history_scoped_and_identity_stable(self):
        token, _ = await self._proof()
        response = await self._submit(token, school_id=2)
        self.assertEqual(response.status_code, 422, response.text)
        response = await self._submit(token, school_id=2, confirm_school_change=True)
        self.assertEqual(response.status_code, 200, response.text)
        async with self.sessions() as db:
            person = await db.get(Staff, self.people["Teacher"].id)
            self.assertEqual(person.school_id, 2)
            self.assertEqual(person.email, self.people["Teacher"].email)
        self.assertEqual((await self.client.get(f"/api/staff/{self.people['Teacher'].id}/application-history", headers=self._headers("Admin"))).status_code, 404)
        response = await self.client.get(f"/api/staff/{self.people['Teacher'].id}/application-history", headers=self._headers("DestinationAdmin"))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(all(event["school_id"] == 2 for event in response.json()))
        self.assertEqual([event["action"] for event in response.json()], ["resubmitted"])
        async with self.sessions() as db:
            old_events = (await db.scalars(select(ApplicationEvent).where(
                ApplicationEvent.staff_id == self.people["Teacher"].id, ApplicationEvent.school_id == 1,
            ))).all()
        self.assertEqual({event.action for event in old_events}, {"reject", "school_change"})

    async def test_change_school_rejects_unapproved_destination_and_teacher_school_profile_edits(self):
        token, _ = await self._proof()
        for destination in (3, 4, 99999):
            response = await self._submit(token, school_id=destination, confirm_school_change=True)
            self.assertEqual(response.status_code, 422, response.text)
        response = await self._submit(token, school={"name": "Unauthorized School", "city": "Iligan"})
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual((await self._submit(token)).status_code, 200)

    async def test_prior_approval_history_or_database_records_prevent_school_change(self):
        for marker_index, marker in enumerate(("approved_at", "last_login_at", "approval_history", "legacy_approved_from", "device", "assignment", "advisory")):
            with self.subTest(marker=marker):
                async with self.sessions() as db:
                    source = self.people["Teacher"]
                    identity = uuid4()
                    person = Staff(
                        id=identity, school_id=1, role="teacher", email=f"{identity}@example.com",
                        password_hash=self.password_hash, first_name="Record", last_name="Applicant",
                        contact_number="09123456789", account_status="rejected", is_active=True,
                        is_approved=False, rejection_reason=REASON,
                    )
                    db.add(person)
                    await db.flush()
                    if marker in {"approved_at", "last_login_at"}:
                        setattr(person, marker, datetime.now(timezone.utc) - timedelta(days=1))
                    elif marker in {"approval_history", "legacy_approved_from"}:
                        db.add(ApplicationEvent(staff_id=identity, school_id=1, action="approve" if marker == "approval_history" else "reject",
                            from_status="pending" if marker == "approval_history" else "approved",
                            to_status="approved" if marker == "approval_history" else "rejected", reason=REASON))
                    elif marker == "device":
                        db.add(StaffDevice(staff_id=identity, device_id="previous-device"))
                    else:
                        grade = GradeLevel(school_id=1, level=marker, name="Grade 7")
                        db.add(grade)
                        await db.flush()
                        section = Section(school_id=1, grade_level_id=grade.grade_level_id, name="Records")
                        db.add(section)
                        await db.flush()
                        assignment_type = SectionAdviser if marker == "advisory" else TeacherAssignment
                        db.add(assignment_type(school_id=1, teacher_id=identity, section_id=section.section_id))
                    await db.commit()
                    self.people["Teacher"] = person
                token, application = await self._proof()
                self.assertFalse(application["can_change_school"])
                response = await self._submit(token, school_id=2, confirm_school_change=True)
                self.assertEqual(response.status_code, 409, response.text)
                response = await self._submit(token, employee_no=f"EMP-{marker}", contact_number=f"0998765432{marker_index}")
                self.assertEqual(response.status_code, 200, response.text)
                self.people["Teacher"] = source

    async def test_duplicate_or_invalid_contact_does_not_consume_correction_token(self):
        token, _ = await self._proof()
        response = await self._submit(token, contact_number="09123456789")
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("contact number", response.json()["detail"])
        response = await self._submit(token, contact_number="abcdefg")
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual((await self._submit(token)).status_code, 200)

    async def test_duplicate_employee_number_does_not_consume_correction_token(self):
        async with self.sessions() as db:
            person = await db.get(Staff, self.people["Approved"].id)
            person.employee_no = "EMP-CONFLICT"
            await db.commit()
        token, _ = await self._proof()
        response = await self._submit(token, employee_no="EMP-CONFLICT")
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual((await self._submit(token, employee_no="EMP-UNIQUE")).status_code, 200)

    async def test_omitted_staff_metadata_is_preserved_and_checked_when_changing_school(self):
        async with self.sessions() as db:
            for name, employee_no, department in (
                ("Teacher", "EMP-EXISTING", "Mathematics"),
                ("Second", "EMP-TRANSFER", "Science"),
                ("Principal", "EMP-PRINCIPAL", "Administration"),
            ):
                person = await db.get(Staff, self.people[name].id)
                person.employee_no, person.department = employee_no, department
            destination_admin = await db.get(Staff, self.people["DestinationAdmin"].id)
            destination_admin.employee_no = "EMP-TRANSFER"
            await db.commit()

        async def submit_without_metadata(token, **extra):
            return await self.client.post("/api/auth/resubmission/submit", json={
                "token": token, "first_name": "Corrected", "last_name": "Applicant",
                "contact_number": f"0998765432{len(self.sent)}", **extra,
            })

        token, _ = await self._proof()
        response = await submit_without_metadata(token)
        self.assertEqual(response.status_code, 200, response.text)
        async with self.sessions() as db:
            person = await db.get(Staff, self.people["Teacher"].id)
            self.assertEqual((person.employee_no, person.department), ("EMP-EXISTING", "Mathematics"))

        token, _ = await self._proof("Second")
        response = await submit_without_metadata(token, school_id=2, confirm_school_change=True)
        self.assertEqual(response.status_code, 409, response.text)
        async with self.sessions() as db:
            destination_admin = await db.get(Staff, self.people["DestinationAdmin"].id)
            destination_admin.employee_no = None
            await db.commit()
        response = await submit_without_metadata(token, school_id=2, confirm_school_change=True)
        self.assertEqual(response.status_code, 200, response.text)
        async with self.sessions() as db:
            person = await db.get(Staff, self.people["Second"].id)
            self.assertEqual(person.school_id, 2)
            self.assertEqual((person.employee_no, person.department), ("EMP-TRANSFER", "Science"))

        token, _ = await self._proof("Principal")
        response = await submit_without_metadata(token, employee_no=None, department=None)
        self.assertEqual(response.status_code, 200, response.text)
        async with self.sessions() as db:
            person = await db.get(Staff, self.people["Principal"].id)
            self.assertIsNone(person.employee_no)
            self.assertIsNone(person.department)

    async def test_principal_corrects_existing_school_and_cannot_move_to_another_school(self):
        token, application = await self._proof("Principal")
        self.assertFalse(application["can_change_school"])
        self.assertEqual(application["school_id"], 3)
        response = await self._submit(token, school_id=2, confirm_school_change=True)
        self.assertEqual(response.status_code, 422, response.text)
        response = await self._submit(token, school_id=3, school={
            "name": "Corrected School Application", "city": "Butuan", "school_type": "public",
        })
        self.assertEqual(response.status_code, 200, response.text)
        async with self.sessions() as db:
            person = await db.get(Staff, self.people["Principal"].id)
            school = await db.get(School, 3)
            self.assertEqual(person.school_id, 3)
            self.assertEqual(person.account_status, "pending")
            self.assertEqual(school.status, "pending")
            self.assertEqual(school.name, "Corrected School Application")
            self.assertIsNone(school.rejection_reason)
            self.assertEqual(await db.scalar(select(func.count()).select_from(School)), 4)

    async def test_principal_cannot_resubmit_duplicate_or_whitespace_school_details(self):
        token, _ = await self._proof("Principal")
        response = await self._submit(token, school={"name": "Original Test School", "city": "Cagayan de Oro"})
        self.assertEqual(response.status_code, 409, response.text)
        for school in ({"name": "   ", "city": "Butuan"}, {"name": "Valid School", "city": "   "}):
            response = await self._submit(token, school=school)
            self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual((await self._submit(token)).status_code, 200)

    async def test_principal_can_open_an_incomplete_legacy_school_application_to_correct_it(self):
        async with self.sessions() as db:
            school = await db.get(School, 3)
            school.city = ""
            await db.commit()
        token, application = await self._proof("Principal")
        self.assertEqual(application["school"]["city"], "")
        response = await self._submit(token, school={"name": "Corrected Legacy School", "city": "Butuan"})
        self.assertEqual(response.status_code, 200, response.text)

    async def test_new_tables_install_additively_and_idempotently_without_changing_existing_accounts(self):
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        added = {ApplicationEvent.__tablename__, ResubmissionChallenge.__tablename__}
        previous_tables = [table for table in Base.metadata.sorted_tables if table.name not in added]
        try:
            async with engine.begin() as connection:
                await connection.run_sync(lambda connection: Base.metadata.create_all(connection, tables=previous_tables))
            identity = uuid4()
            async with sessions() as db:
                db.add(School(school_id=91, name="Preserved Existing School", city="Iligan", status="approved"))
                await db.flush()
                db.add(Staff(id=identity, school_id=91, email="preserved@example.com", role="teacher",
                    password_hash=self.password_hash, first_name="Existing", last_name="Teacher",
                    contact_number="09123456789", account_status="rejected", rejection_reason=REASON))
                await db.commit()
            for _ in range(2):
                async with engine.begin() as connection:
                    await connection.run_sync(Base.metadata.create_all)
            async with sessions() as db:
                person = await db.get(Staff, identity)
                self.assertEqual(person.school_id, 91)
                self.assertEqual(person.email, "preserved@example.com")
                self.assertEqual(person.account_status, "rejected")
                self.assertEqual(person.rejection_reason, REASON)
                self.assertEqual(await db.scalar(select(func.count()).select_from(Staff)), 1)
                self.assertEqual(await db.scalar(select(func.count()).select_from(School)), 1)
                self.assertEqual(await db.scalar(select(func.count()).select_from(ApplicationEvent)), 0)
                self.assertEqual(await db.scalar(select(func.count()).select_from(ResubmissionChallenge)), 0)
        finally:
            await engine.dispose()


if __name__ == "__main__":
    unittest.main()
