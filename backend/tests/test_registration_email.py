"""Email verification before registration, using synthetic data and fake mail."""
from __future__ import annotations

from datetime import timedelta
import asyncio
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
from sqlalchemy import event, func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.engine import make_url

import app.models
from app.api import deps
from app.api.routers import auth, registration_email, schools, staff
from app.core.config import settings
from app.db.session import Base, get_db
from app.models.registration_email import RegistrationEmailChallenge, RegistrationEmailLimit
from app.models.schools import School
from app.models.staff import Staff
from app.services.email import EmailDeliveryError
from app.services.registration_email import now_utc

PASSWORD = "SyntheticPassword1!"
CODE = "654321"


class RegistrationEmailTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False, autoflush=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with self.sessions() as db:
            db.add(School(school_id=1, name="Synthetic Approved School", city="Lala", status="approved"))
            await db.commit()
        async def isolated_db():
            async with self.sessions() as db:
                try:
                    yield db
                except Exception:
                    await db.rollback()
                    raise
        async def no_throttle():
            return None
        application = FastAPI()
        self.app = application
        for router in (auth.router, schools.router, registration_email.router, staff.router):
            application.include_router(router)
        application.dependency_overrides[get_db] = isolated_db
        application.dependency_overrides[deps.limit_register] = no_throttle
        application.dependency_overrides[deps.limit_otp] = no_throttle
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application, client=("198.51.100.7", 45000)),
            base_url="http://testserver",
        )
        for patcher in (
            patch.multiple(settings, DEBUG_OTP=True, LOG_OTP_CODES=True, OTP_MAX_ATTEMPTS=5),
            patch.object(registration_email, "generate_otp", return_value=CODE),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.verify_captcha = AsyncMock()
        self.send = AsyncMock()
        for owner, field, replacement in (
            (registration_email, "verify_captcha", self.verify_captcha),
            (registration_email, "send_registration_verification_email", self.send),
            (auth, "async_get_password_hash", AsyncMock(return_value="synthetic-hash")),
            (schools, "async_get_password_hash", AsyncMock(return_value="synthetic-hash")),
        ):
            patcher = patch.object(owner, field, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)
        deps._BUCKETS.clear()

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.engine.dispose()
        deps._BUCKETS.clear()

    def teacher(self, email="applicant@example.com", token=None):
        return {"school_id": 1, "email": email, "password": PASSWORD,
                "first_name": "Synthetic", "last_name": "Applicant", "contact_number": "09123456789",
                "email_verification_token": token}

    def school(self, email="principal@example.com", token=None):
        return {"school": {"name": "Synthetic New School", "city": "Lala", "school_type": "public"},
                "email": email, "password": PASSWORD, "first_name": "Synthetic", "last_name": "Principal",
                "contact_number": "09987654321", "email_verification_token": token}

    async def test_invalid_mobile_numbers_are_rejected_before_registration(self):
        for number in ("abcdefg", "0912345678", "091234567890", "08123456789", "09123a56789",
                       "0917 000 0000", "+639123456789", "09１２３４５６７８９"):
            for endpoint, payload in (("/api/auth/register", self.teacher()),
                                      ("/api/schools/register", self.school())):
                with self.subTest(endpoint=endpoint, number=number):
                    response = await self.client.post(endpoint, json={**payload, "contact_number": number})
                    self.assertEqual(response.status_code, 422, response.text)
                    self.assertEqual(response.json()["detail"][0]["loc"], ["body", "contact_number"])

    async def test_school_name_is_normalized_on_save_and_legacy_directory_reads(self):
        token, _ = await self.proof("principal@example.com", "school")
        payload = self.school(token=token)
        payload["school"]["name"] = "  CAMP   EVANGELISTA ES  "
        response = await self.client.post("/api/schools/register", json=payload)
        self.assertEqual(response.status_code, 201, response.text)
        async with self.sessions() as db:
            created = await db.get(School, response.json()["school_id"])
            self.assertEqual(created.name, "Camp Evangelista Es")
            created.status = "approved"
            (await db.get(School, 1)).name = "GUMAGAMOT ELEMENTARY SCHOOL"
            await db.commit()
        response = await self.client.get("/api/schools/directory")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual({row["name"] for row in response.json()}, {
            "Camp Evangelista Es", "Gumagamot Elementary School",
        })

    async def test_school_name_requires_letters(self):
        for name in ("123456", " --- "):
            payload = self.school()
            payload["school"]["name"] = name
            response = await self.client.post("/api/schools/register", json=payload)
            self.assertEqual(response.status_code, 422, response.text)
            self.assertEqual(response.json()["detail"][0]["loc"], ["body", "school", "name"])

    async def test_duplicate_contacts_across_registration_roles_preserve_email_proof(self):
        for first_kind, second_kind, number in (("teacher", "school", "09111111111"),
                                               ("school", "teacher", "09222222222")):
            with self.subTest(first=first_kind):
                first_email, second_email = f"first-{first_kind}@example.com", f"second-{first_kind}@example.com"
                first_token, _ = await self.proof(first_email, first_kind)
                make_first = self.teacher if first_kind == "teacher" else self.school
                first_payload = {**make_first(first_email, first_token), "contact_number": number}
                first_endpoint = "/api/auth/register" if first_kind == "teacher" else "/api/schools/register"
                response = await self.client.post(first_endpoint, json=first_payload)
                self.assertEqual(response.status_code, 201, response.text)
                second_token, challenge = await self.proof(second_email, second_kind)
                make_second = self.teacher if second_kind == "teacher" else self.school
                second_payload = {**make_second(second_email, second_token), "contact_number": number}
                second_endpoint = "/api/auth/register" if second_kind == "teacher" else "/api/schools/register"
                if second_kind == "school":
                    second_payload["school"]["name"] = "Another Synthetic School"
                response = await self.client.post(second_endpoint, json=second_payload)
                self.assertEqual(response.status_code, 409, response.text)
                self.assertEqual(response.json()["detail"], "An account with that contact number already exists.")
                async with self.sessions() as db:
                    self.assertIsNone((await db.get(RegistrationEmailChallenge, UUID(challenge))).used_at)

    async def test_legacy_phone_formats_cannot_bypass_duplicate_check(self):
        async with self.sessions() as db:
            person = Staff(school_id=1, email="legacy@example.com", password_hash="unused",
                           first_name="Legacy", last_name="Test", contact_number="09123456789", role="teacher")
            db.add(person)
            await db.commit()
            identity = person.id
        token, _ = await self.proof()
        for value in ("\t09123456789\n", "0912 345 6789", "+63 912 345 6789", "(0912)345-6789"):
            with self.subTest(value=value):
                async with self.sessions() as db:
                    (await db.get(Staff, identity)).contact_number = value
                    await db.commit()
                response = await self.client.post("/api/auth/register", json=self.teacher(token=token))
                self.assertEqual(response.status_code, 409, response.text)
                self.assertIn("contact number", response.json()["detail"])

    async def test_profile_contact_cannot_bypass_uniqueness_and_accepts_own_number(self):
        async with self.sessions() as db:
            people = []
            for index in (1, 2):
                person = Staff(school_id=1, email=f"profile{index}@example.com", password_hash="unused",
                               first_name="Profile", last_name="Test", contact_number=f"0911111111{index}",
                               role="teacher", account_status="approved", is_approved=True)
                db.add(person)
                people.append(person)
            await db.commit()
            identity = people[0].id
        async def current(db=Depends(get_db)):
            return await db.get(Staff, identity)
        self.app.dependency_overrides[deps.get_current_user] = current
        for number, expected in (("09111111112", 409), ("09111111111", 200), ("09111abc111", 422)):
            response = await self.client.patch("/api/staff/me", json={"contact_number": number})
            self.assertEqual(response.status_code, expected, response.text)
        response = await self.client.patch("/api/staff/me", json={"first_name": "Updated"})
        self.assertEqual(response.status_code, 200, response.text)

    async def test_school_settings_normalize_names_and_reject_numbers_only(self):
        self.app.dependency_overrides[deps.school_admin_scope] = lambda: 1
        response = await self.client.patch("/api/schools/me", json={"name": "123456"})
        self.assertEqual(response.status_code, 422, response.text)
        response = await self.client.patch("/api/schools/me", json={"name": "  ST. MARY'S SCHOOL 2  "})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["name"], "St. Mary's School 2")
        async with self.sessions() as db:
            self.assertEqual((await db.get(School, 1)).name, "St. Mary's School 2")

    async def request(self, email="applicant@example.com", kind="teacher"):
        return await self.client.post("/api/auth/registration-email/request", json={
            "email": email, "kind": kind, "captcha_token": "synthetic-captcha",
        })

    async def proof(self, email="applicant@example.com", kind="teacher"):
        response = await self.request(email, kind)
        self.assertEqual(response.status_code, 202, response.text)
        challenge = response.json()["challenge_id"]
        response = await self.verify(challenge, email=email)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["expires_in"], 900)
        return response.json()["email_verification_token"], challenge

    async def verify(self, challenge, *, email="applicant@example.com", code=CODE):
        return await self.client.post("/api/auth/registration-email/verify", json={
            "email": email, "challenge_id": challenge, "code": code,
        })

    async def mutate_challenge(self, identity, **values):
        async with self.sessions() as db:
            row = await db.get(RegistrationEmailChallenge, UUID(identity))
            for key, value in values.items():
                setattr(row, key, value)
            await db.commit()

    async def count(self, model):
        async with self.sessions() as db:
            return await db.scalar(select(func.count()).select_from(model))

    async def test_send_creates_only_challenge_never_account_and_never_exposes_debug_code(self):
        response = await self.request("UPPER@EXAMPLE.COM")
        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(response.json()["expires_in"], 300)
        self.assertNotIn(CODE, response.text)
        self.assertEqual(await self.count(Staff), 0)
        self.assertEqual(await self.count(School), 1)
        self.send.assert_awaited_once_with("upper@example.com", CODE, kind="teacher")
        self.verify_captcha.assert_awaited_once_with("synthetic-captcha", action="register_teacher")
        async with self.sessions() as db:
            row = await db.scalar(select(RegistrationEmailChallenge))
            self.assertNotEqual(row.code_digest, CODE)
            self.assertIsNone(row.token_digest)
            self.assertLessEqual((row.expires_at.replace(tzinfo=now_utc().tzinfo) - now_utc()).total_seconds(), 300)

    async def test_registration_requires_proof_even_when_captcha_disabled(self):
        for path, payload in (("/api/auth/register", self.teacher()), ("/api/schools/register", self.school())):
            response = await self.client.post(path, json=payload)
            self.assertEqual(response.status_code, 400, response.text)
            self.assertEqual(response.json()["detail"]["code"], "email_verification_required")
        self.assertEqual(await self.count(Staff), 0)
        self.assertEqual(await self.count(School), 1)

    async def test_verified_teacher_is_saved_pending_and_proof_consumed_once(self):
        token, challenge = await self.proof()
        response = await self.client.post("/api/auth/register", json=self.teacher(token=token))
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["account_status"], "pending")
        self.assertFalse(response.json()["is_approved"])
        response = await self.client.post("/api/auth/register", json=self.teacher(token=token))
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(await self.count(Staff), 1)
        async with self.sessions() as db:
            row = await db.get(RegistrationEmailChallenge, UUID(challenge))
            self.assertIsNotNone(row.used_at)

    async def test_verified_school_creates_principal_and_school_pending(self):
        token, _ = await self.proof("principal@example.com", "school")
        self.verify_captcha.assert_awaited_once_with("synthetic-captcha", action="register_school")
        response = await self.client.post("/api/schools/register", json=self.school(token=token))
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["status"], "pending")
        async with self.sessions() as db:
            person = await db.scalar(select(Staff))
            self.assertEqual(person.role, "admin")
            self.assertEqual(person.account_status, "pending")
            self.assertFalse(person.is_approved)
            self.assertEqual((await db.get(School, person.school_id)).status, "pending")

    async def test_wrong_email_and_registration_kind_cannot_reuse_proof(self):
        token, _ = await self.proof()
        wrong_email = await self.client.post("/api/auth/register", json=self.teacher("different@example.com", token))
        wrong_kind = await self.client.post("/api/schools/register", json=self.school("applicant@example.com", token))
        for response in (wrong_email, wrong_kind):
            self.assertEqual(response.status_code, 400, response.text)
            self.assertEqual(response.json()["detail"]["code"], "email_verification_required")
        self.assertEqual(await self.count(Staff), 0)
        response = await self.client.post("/api/auth/register", json=self.teacher(token=token))
        self.assertEqual(response.status_code, 201, response.text)

    async def test_expired_proof_does_not_create_an_account(self):
        token, challenge = await self.proof()
        await self.mutate_challenge(challenge, token_expires_at=now_utc() - timedelta(seconds=1))
        response = await self.client.post("/api/auth/register", json=self.teacher(token=token))
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(await self.count(Staff), 0)

    async def test_expired_code_and_wrong_email_cannot_verify(self):
        response = await self.request()
        challenge = response.json()["challenge_id"]
        response = await self.verify(challenge, email="different@example.com")
        self.assertEqual(response.status_code, 400, response.text)
        await self.mutate_challenge(challenge, expires_at=now_utc() - timedelta(seconds=1))
        response = await self.verify(challenge)
        self.assertEqual(response.status_code, 400, response.text)

    async def test_five_wrong_codes_block_even_the_correct_code(self):
        challenge = (await self.request()).json()["challenge_id"]
        for _ in range(5):
            response = await self.verify(challenge, code="000000")
            self.assertEqual(response.status_code, 400, response.text)
        response = await self.verify(challenge)
        self.assertEqual(response.status_code, 400, response.text)
        async with self.sessions() as db:
            row = await db.get(RegistrationEmailChallenge, UUID(challenge))
            self.assertEqual(row.attempts, 5)
            self.assertIsNone(row.token_digest)

    async def test_verified_code_is_one_use_and_token_is_not_stored_in_plaintext(self):
        token, challenge = await self.proof()
        response = await self.verify(challenge)
        self.assertEqual(response.status_code, 400, response.text)
        async with self.sessions() as db:
            row = await db.get(RegistrationEmailChallenge, UUID(challenge))
            self.assertNotEqual(row.token_digest, token)

    async def test_captcha_failure_and_outage_create_nothing(self):
        for status_code, code in ((400, "captcha_invalid"), (503, "captcha_unavailable")):
            self.verify_captcha.side_effect = HTTPException(status_code, detail={"code": code})
            response = await self.request()
            self.assertEqual(response.status_code, status_code, response.text)
        self.send.assert_not_awaited()
        self.assertEqual(await self.count(RegistrationEmailChallenge), 0)
        self.assertEqual(await self.count(RegistrationEmailLimit), 0)

    async def test_provider_failure_invalidates_code_without_creating_account(self):
        self.send.side_effect = EmailDeliveryError("private-provider-detail")
        response = await self.request()
        self.assertEqual(response.status_code, 503, response.text)
        self.assertNotIn("private-provider-detail", response.text)
        async with self.sessions() as db:
            row = await db.scalar(select(RegistrationEmailChallenge))
            self.assertIsNotNone(row.used_at)
        self.assertEqual(await self.count(Staff), 0)

    async def test_recipient_cooldown_persists_and_counts_case_insensitively(self):
        first = await self.request()
        self.assertEqual(first.status_code, 202, first.text)
        response = await self.request("APPLICANT@EXAMPLE.COM", "school")
        self.assertEqual(response.status_code, 429, response.text)
        self.assertGreaterEqual(int(response.headers["Retry-After"]), 1)
        self.send.assert_awaited_once()

    async def test_network_limit_cannot_be_evaded_with_forwarded_headers(self):
        with patch.object(settings, "AUTH_REGISTER_IP_LIMIT", 1):
            response = await self.request()
            self.assertEqual(response.status_code, 202, response.text)
            response = await self.client.post("/api/auth/registration-email/request", json={
                "email": "another@example.com", "kind": "teacher",
            }, headers={"X-Forwarded-For": "203.0.113.22"})
            self.assertEqual(response.status_code, 429, response.text)

    async def test_invalid_school_rolls_back_proof_for_corrected_submission(self):
        token, challenge = await self.proof()
        payload = self.teacher(token=token)
        payload["school_id"] = 999
        response = await self.client.post("/api/auth/register", json=payload)
        self.assertEqual(response.status_code, 400, response.text)
        async with self.sessions() as db:
            self.assertIsNone((await db.get(RegistrationEmailChallenge, UUID(challenge))).used_at)
        response = await self.client.post("/api/auth/register", json=self.teacher(token=token))
        self.assertEqual(response.status_code, 201, response.text)

    async def test_case_insensitive_duplicate_after_verification_does_not_consume_proof(self):
        token, challenge = await self.proof()
        async with self.sessions() as db:
            db.add(Staff(school_id=1, email="APPLICANT@EXAMPLE.COM", password_hash="synthetic",
                         first_name="Existing", last_name="Account", contact_number="09123456789", role="teacher"))
            await db.commit()
        response = await self.client.post("/api/auth/register", json=self.teacher(token=token))
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(await self.count(Staff), 1)
        async with self.sessions() as db:
            self.assertIsNone((await db.get(RegistrationEmailChallenge, UUID(challenge))).used_at)

    async def test_email_provider_and_password_hash_do_not_hold_database_connection(self):
        checked_out = 0
        def checkout(*_):
            nonlocal checked_out
            checked_out += 1
        def checkin(*_):
            nonlocal checked_out
            checked_out -= 1
        event.listen(self.engine.sync_engine, "checkout", checkout)
        event.listen(self.engine.sync_engine, "checkin", checkin)
        async def send_without_connection(*_, **__):
            self.assertEqual(checked_out, 0, "Mail must release the database pool first")
        async def hash_without_connection(*_, **__):
            self.assertEqual(checked_out, 0, "Password hashing must release the database pool first")
            return "synthetic-hash"
        self.send.side_effect = send_without_connection
        teacher_token, _ = await self.proof()
        school_token, _ = await self.proof("principal@example.com", "school")
        with patch.object(auth, "async_get_password_hash", side_effect=hash_without_connection), \
                patch.object(schools, "async_get_password_hash", side_effect=hash_without_connection):
            response = await self.client.post("/api/auth/register", json=self.teacher(token=teacher_token))
            self.assertEqual(response.status_code, 201, response.text)
            response = await self.client.post("/api/schools/register", json=self.school(token=school_token))
            self.assertEqual(response.status_code, 201, response.text)


@unittest.skipUnless(os.environ.get("TRACE_TEST_POSTGRES_URL"), "Set TRACE_TEST_POSTGRES_URL for isolated PostgreSQL races")
class RegistrationEmailPostgresRaceTests(unittest.IsolatedAsyncioTestCase):
    """Use a random schema on an explicitly supplied loopback-only test DB."""

    async def asyncSetUp(self):
        connection_url = make_url(os.environ["TRACE_TEST_POSTGRES_URL"])
        if connection_url.host not in {"127.0.0.1", "localhost", "::1"}:
            self.skipTest("PostgreSQL race tests require an isolated loopback database")
        self.schema = "registration_email_test_" + uuid4().hex
        self.admin_engine = create_async_engine(connection_url)
        async with self.admin_engine.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{self.schema}"'))
        self.engine = create_async_engine(connection_url, connect_args={"server_settings": {"search_path": self.schema}})
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False, autoflush=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with self.sessions() as db:
            db.add(School(school_id=1, name="Synthetic Race School", city="Lala", status="approved"))
            await db.commit()
        async def isolated_db():
            async with self.sessions() as db:
                try:
                    yield db
                except Exception:
                    await db.rollback()
                    raise
        async def no_throttle():
            return None
        application = FastAPI()
        application.include_router(registration_email.router)
        application.include_router(auth.router)
        application.dependency_overrides[get_db] = isolated_db
        application.dependency_overrides[deps.limit_register] = no_throttle
        application.dependency_overrides[deps.limit_otp] = no_throttle
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://testserver")
        for owner, name, replacement in (
            (registration_email, "generate_otp", lambda: CODE),
            (registration_email, "verify_captcha", AsyncMock()),
            (registration_email, "send_registration_verification_email", AsyncMock()),
            (auth, "async_get_password_hash", AsyncMock(return_value="synthetic")),
        ):
            patcher = patch.object(owner, name, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.engine.dispose()
        async with self.admin_engine.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA "{self.schema}" CASCADE'))
        await self.admin_engine.dispose()

    async def request_code(self):
        response = await self.client.post("/api/auth/registration-email/request", json={
            "email": "race@example.com", "kind": "teacher",
        })
        self.assertEqual(response.status_code, 202, response.text)
        return {"challenge_id": response.json()["challenge_id"], "email": "race@example.com", "code": CODE}

    async def test_concurrent_recipient_requests_send_only_one_email(self):
        responses = await asyncio.wait_for(asyncio.gather(*[
            self.client.post("/api/auth/registration-email/request", json={"email": "race@example.com", "kind": kind})
            for kind in ("teacher", "school")
        ]), timeout=10)
        self.assertEqual(sorted(response.status_code for response in responses), [202, 429])
        registration_email.send_registration_verification_email.assert_awaited_once()

    async def test_concurrent_code_verification_issues_only_one_proof(self):
        payload = await self.request_code()
        responses = await asyncio.wait_for(asyncio.gather(*[
            self.client.post("/api/auth/registration-email/verify", json=payload) for _ in range(2)
        ]), timeout=10)
        self.assertEqual(sorted(response.status_code for response in responses), [200, 400])

    async def test_concurrent_registration_consumes_proof_once(self):
        verified = await self.client.post("/api/auth/registration-email/verify", json=await self.request_code())
        self.assertEqual(verified.status_code, 200, verified.text)
        payload = {"school_id": 1, "email": "race@example.com", "password": PASSWORD,
                   "first_name": "Synthetic", "last_name": "Race", "contact_number": "09123456789",
                   "email_verification_token": verified.json()["email_verification_token"]}
        responses = await asyncio.wait_for(asyncio.gather(*[
            self.client.post("/api/auth/register", json=payload) for _ in range(2)
        ]), timeout=10)
        self.assertEqual(sorted(response.status_code for response in responses), [201, 400])
        async with self.sessions() as db:
            self.assertEqual(await db.scalar(select(func.count()).select_from(Staff)), 1)


if __name__ == "__main__":
    unittest.main()
