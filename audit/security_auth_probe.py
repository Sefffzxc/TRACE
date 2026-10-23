"""Reproduce auth findings using only a fresh audit-owned SQLite database.

Run from repo root: backend/.venv/Scripts/python.exe audit/security_auth_probe.py
Never connects to the existing application database. No credentials/tokens are
printed or written to results. Fixtures are invented example.com identities.
"""
from __future__ import annotations

import asyncio
import contextlib
import io
import json
import logging
import os
from pathlib import Path
import secrets
import sys
import uuid
from datetime import datetime, timedelta, timezone

ROOT = Path(__file__).resolve().parents[1]
RUN_ID = uuid.uuid4().hex
DB = ROOT / "audit" / f"security-auth-{RUN_ID}.sqlite"
UPLOADS = ROOT / "audit" / f"security-auth-uploads-{RUN_ID}"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{DB.as_posix()}"
os.environ["UPLOAD_DIR"] = UPLOADS.as_posix()
os.environ["SECRET_KEY"] = secrets.token_urlsafe(48)
os.environ["DEBUG_OTP"] = "true"
os.environ["AI_ENABLED"] = "false"
sys.path.insert(0, str(ROOT / "backend"))

import httpx
from sqlalchemy import select, func
from app.main import app
from app.core.config import settings
from app.core.security import get_password_hash, verify_password
from app.db.session import AsyncSessionLocal, async_engine, init_models
from app.models.schools import School
from app.models.staff import Staff, StaffDevice, OTPCode, PasswordReset, StaffSession, AuditLog

PASSWORD = "Audit-Only-Strong-Password1!"
NEW_PASSWORD = "Audit-New-Strong-Password2!"
results = {}
users = {}
# Expected invalid-input probes raise DB errors; suppress exception logs that
# contain fixture password hashes/PII. Results retain status + exception type.
logging.disable(logging.CRITICAL)


def put(name, **detail):
    results[name] = detail


async def setup():
    await init_models()
    async with AsyncSessionLocal() as db:
        schools = [School(name=f"Audit School {n}", city="Test City", status="approved") for n in (1, 2)]
        db.add_all(schools)
        await db.flush()
        pw_hash = get_password_hash(PASSWORD)
        for name, role, school in [
            ("teacher", "teacher", schools[0]), ("admin", "admin", schools[0]),
            ("other", "admin", schools[1]), ("operator", "super_admin", None),
            ("victim", "teacher", schools[0]), ("otp-race", "teacher", schools[0]),
            ("reset-race", "teacher", schools[0]), ("cap", "teacher", schools[0]),
            ("stale-otp", "teacher", schools[0]),
            ("login-race", "teacher", schools[0]),
        ]:
            user = Staff(email=f"audit-{name}@example.com", password_hash=pw_hash,
                         first_name="Audit", last_name=name, contact_number="09170000000",
                         school_id=school.school_id if school else None, role=role,
                         is_active=True, is_approved=True, account_status="approved")
            db.add(user)
            await db.flush()
            users[name] = {"id": str(user.id), "school_id": user.school_id, "email": user.email}
        await db.commit()


async def main():
    await setup()
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://audit.local") as client:
        async def req(method, path, data=None, token=None, ip=None, **kwargs):
            headers = {}
            if token:
                headers["Authorization"] = f"Bearer {token}"
            if ip:
                headers["X-Forwarded-For"] = ip
            return await client.request(method, path, json=data, headers=headers, **kwargs)

        async def login(name, password=PASSWORD, device="audit-device"):
            r = await req("POST", "/api/auth/login", {"email": users[name]["email"], "password": password, "device_id": device}, ip=secrets.token_hex(4))
            data = r.json()
            if data.get("requires_2fa"):
                r = await req("POST", "/api/auth/verify-otp", {"user_id": data["user_id"], "code": data["debug_otp"], "device_id": device}, ip=secrets.token_hex(4))
                data = r.json()
            assert r.status_code == 201, (name, r.status_code)
            return data

        teacher = await login("teacher")
        admin = await login("admin")
        operator = await login("operator")
        old_victim = await login("victim")

        # The anonymous reset response supplies every secret needed for takeover.
        r = await req("POST", "/api/auth/forgot-password", {"email": users["victim"]["email"]})
        exposed = bool(r.json().get("debug_otp"))
        r2 = await req("POST", "/api/auth/verify-reset-otp", {"email": users["victim"]["email"], "code": r.json().get("debug_otp")})
        r3 = await req("POST", "/api/auth/reset-password", {"token": r2.json()["token"], "new_password": NEW_PASSWORD, "confirm_password": NEW_PASSWORD})
        taken = await login("victim", NEW_PASSWORD, "new-device-after-reset")
        put("anonymous_reset_takeover", otp_exposed=exposed, exchange_status=r2.status_code,
            reset_status=r3.status_code, victim_account_login_succeeded=bool(taken.get("access_token")))

        # Session flags written by logout are never checked by auth dependencies.
        r = await req("POST", "/api/auth/logout", token=teacher["access_token"])
        read_after = await req("GET", "/api/auth/me", token=teacher["access_token"])
        refresh_after = await req("POST", "/api/auth/refresh", token=teacher["refresh_token"])
        reset_old = await req("GET", "/api/auth/me", token=old_victim["access_token"])
        reset_refresh = await req("POST", "/api/auth/refresh", token=old_victim["refresh_token"])
        put("logout_reset_revocation", logout_status=r.status_code, logged_out_access_status=read_after.status_code,
            logged_out_refresh_status=refresh_after.status_code, pre_reset_access_status=reset_old.status_code,
            pre_reset_refresh_status=reset_refresh.status_code)

        # Reset OTP has no account/code attempt cap, even though login OTP does.
        r = await req("POST", "/api/auth/forgot-password", {"email": users["cap"]["email"]}, ip="192.0.2.10")
        code = r.json()["debug_otp"]
        wrong = "111111" if code != "111111" else "222222"
        attempts = [await req("POST", "/api/auth/verify-reset-otp", {"email": users["cap"]["email"], "code": wrong}, ip=f"192.0.2.{i+20}") for i in range(12)]
        valid = await req("POST", "/api/auth/verify-reset-otp", {"email": users["cap"]["email"], "code": code}, ip="192.0.2.40")
        put("reset_attempt_limit", wrong_attempt_statuses=[r.status_code for r in attempts],
            correct_code_after_12_failures_status=valid.status_code)

        # The application's own rate limiter trusts arbitrary forwarding headers.
        missing = {"email": "audit-nonexistent@example.com", "password": "invalid", "device_id": "audit-rate"}
        baseline = [await req("POST", "/api/auth/login", missing, ip="198.51.100.10") for _ in range(11)]
        changed = await req("POST", "/api/auth/login", missing, ip="198.51.100.11")
        put("rate_limit_forwarding_spoof", statuses_same_claimed_ip=[r.status_code for r in baseline],
            changed_claimed_ip_status=changed.status_code)

        # An admin-issued temporary password does not limit an existing token.
        r = await req("PATCH", f'/api/staff/{users["teacher"]["id"]}', {"action": "force_reset"}, token=admin["access_token"])
        forced_user = await login("teacher", r.json()["temporary_password"], "force-reset-device")
        data_before_change = await req("GET", "/api/staff/teachers", token=forced_user["access_token"])
        old_data = await req("GET", "/api/staff/teachers", token=teacher["access_token"])
        put("forced_password_change_gate", force_reset_status=r.status_code,
            requires_password_change=forced_user.get("requires_password_change"),
            protected_data_before_password_change_status=data_before_change.status_code,
            pre_force_reset_access_status=old_data.status_code)

        # Refresh does not enforce one-time use of an old refresh token.
        await asyncio.sleep(1.1)  # JWT timestamps have second precision.
        first = await req("POST", "/api/auth/refresh", token=admin["refresh_token"])
        again = await req("POST", "/api/auth/refresh", token=admin["refresh_token"])
        put("refresh_rotation", first_refresh_status=first.status_code, old_refresh_replayed_status=again.status_code)

        # Code consumption uses SELECT followed by UPDATE without atomic claiming.
        r = await req("POST", "/api/auth/login", {"email": users["otp-race"]["email"], "password": PASSWORD, "device_id": "otp-race"}, ip="203.0.113.10")
        code = r.json()["debug_otp"]
        races = await asyncio.gather(*[req("POST", "/api/auth/verify-otp", {"user_id": users["otp-race"]["id"], "code": code, "device_id": f"race-device-{i}"}, ip=f"203.0.113.{20+i}") for i in range(4)])
        put("concurrent_login_otp_replay", statuses=[r.status_code for r in races], tokens_issued=sum(bool(r.json().get("access_token")) for r in races))

        r = await req("POST", "/api/auth/forgot-password", {"email": users["reset-race"]["email"]}, ip="203.0.113.40")
        code = r.json()["debug_otp"]
        races = await asyncio.gather(*[req("POST", "/api/auth/verify-reset-otp", {"email": users["reset-race"]["email"], "code": code}, ip=f"203.0.113.{50+i}") for i in range(4)])
        put("concurrent_reset_otp_replay", statuses=[r.status_code for r in races], reset_tokens_issued=sum(bool(r.json().get("token")) for r in races))

        # New OTP requests do not retire older codes; they become active again.
        r1 = await req("POST", "/api/auth/login", {"email": users["stale-otp"]["email"], "password": PASSWORD, "device_id": "stale-device"}, ip="203.0.113.70")
        await asyncio.sleep(1.1)
        r2 = await req("POST", "/api/auth/login", {"email": users["stale-otp"]["email"], "password": PASSWORD, "device_id": "stale-device"}, ip="203.0.113.71")
        newest = await req("POST", "/api/auth/verify-otp", {"user_id": users["stale-otp"]["id"], "code": r2.json()["debug_otp"], "device_id": "stale-device"}, ip="203.0.113.72")
        older = await req("POST", "/api/auth/verify-otp", {"user_id": users["stale-otp"]["id"], "code": r1.json()["debug_otp"], "device_id": "stale-device"}, ip="203.0.113.73")
        put("old_login_otp_resurrection", newest_code_status=newest.status_code, previous_code_after_new_code_used_status=older.status_code)

        # Positive role/tenant checks, plus documented teacher/admin profile gap.
        put("boundary_checks",
            anonymous_staff_status=(await req("GET", "/api/staff")).status_code,
            teacher_admin_mutation_status=(await req("PATCH", f'/api/staff/{users["admin"]["id"]}', {"action": "deactivate"}, token=teacher["access_token"])).status_code,
            admin_other_school_profile_status=(await req("GET", f'/api/staff/{users["other"]["id"]}', token=admin["access_token"])).status_code,
            operator_students_status=(await req("GET", "/api/students", token=operator["access_token"])).status_code,
            teacher_admin_directory_status=(await req("GET", "/api/staff?role=admin", token=teacher["access_token"])).status_code,
            teacher_admin_profile_status=(await req("GET", f'/api/staff/{users["admin"]["id"]}', token=teacher["access_token"])).status_code)

        # Endpoint bypasses advertised password policy for school principals.
        weak = await req("POST", "/api/schools/register", {"school": {"name": "Audit Weak Principal School", "city": "Test City"}, "first_name": "Audit", "last_name": "Principal", "email": "audit-weak@example.com", "contact_number": "09170000000", "password": "aaaaaaaa"}, ip="192.0.2.80")
        put("principal_weak_password", weak_password_registration_status=weak.status_code)

        # Existing identity with uppercase local-part passes the duplicate lookup.
        duplicate = await req("POST", "/api/auth/register", {"school_id": users["teacher"]["school_id"], "email": users["teacher"]["email"].upper(), "password": PASSWORD, "first_name": "Audit", "last_name": "Duplicate", "contact_number": "09170000000"}, ip="192.0.2.81")
        put("case_duplicate_registration", duplicate_status=duplicate.status_code, error_type=duplicate.json().get("error_type"))

        null_school = await req("PATCH", "/api/schools/me", {"passing_mark": None}, token=admin["access_token"])
        put("null_school_setting", null_passing_mark_status=null_school.status_code, error_type=null_school.json().get("error_type"))

        # Upload accepts a MIME claim without decoding/verifying image bytes.
        uploaded = await client.post("/api/staff/me/photo", headers={"Authorization": f'Bearer {teacher["access_token"]}'}, files={"file": ("fake.png", b"this is not a valid image", "image/png")})
        public_status = None
        if uploaded.status_code == 200:
            public_status = (await client.get(uploaded.json()["profile_pic"])).status_code
        put("invalid_image_and_public_upload", invalid_image_upload_status=uploaded.status_code,
            anonymous_upload_read_status=public_status)

        # Bcrypt silently treats distinct passwords with same first 72 bytes equal.
        prefix = "A1!" + "a" * 69
        hashed = get_password_hash(prefix + "Original")
        put("bcrypt_truncation", distinct_long_password_accepted=verify_password(prefix + "Different", hashed))

        # Events attributed only to a target user disappear from the admin view.
        audit_view = await req("GET", "/api/staff/audit-logs?action=password_reset_requested", token=admin["access_token"])
        async with AsyncSessionLocal() as db:
            actual = await db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == "password_reset_requested"))
        put("security_events_hidden_from_admin", stored_reset_request_events=actual,
            reset_request_events_visible_to_admin=len(audit_view.json()))

        # A healthy settings update is also logged without the actor.
        healthy_update = await req("PATCH", "/api/schools/me", {"short_name": "Audit Changed"}, token=admin["access_token"])
        audit_view = await req("GET", "/api/staff/audit-logs?action=school_settings_updated", token=admin["access_token"])
        put("settings_audit_missing_actor", update_status=healthy_update.status_code,
            settings_events_visible_to_admin=len(audit_view.json()))

        # Default config accepts its public placeholder as the JWT signing key.
        # This is conditional on an operator failing to override SECRET_KEY;
        # Render's blueprint does override it. Restore the audit key afterwards.
        from app.core.config import Settings
        from jose import jwt
        audit_key = settings.SECRET_KEY
        settings.SECRET_KEY = Settings.model_fields["SECRET_KEY"].default
        forged = jwt.encode({"sub": users["teacher"]["id"], "type": "access", "exp": datetime.now(timezone.utc) + timedelta(minutes=1)}, settings.SECRET_KEY, algorithm="HS256")
        forged_response = await req("GET", "/api/auth/me", token=forged)
        settings.SECRET_KEY = audit_key
        put("default_signing_key", known_default_key_forged_access_status=forged_response.status_code,
            precondition="SECRET_KEY left at source default; not the generated Render key")

        # Public forgot/login responses distinguish registered addresses.
        known = await req("POST", "/api/auth/login", {"email": users["victim"]["email"], "password": "wrong", "device_id": "audit-enumeration"}, ip="198.51.100.50")
        unknown = await req("POST", "/api/auth/login", missing, ip="198.51.100.51")
        put("login_account_enumeration", known_status=known.status_code, unknown_status=unknown.status_code,
            response_messages_differ=known.json().get("detail") != unknown.json().get("detail"))

        # Field bounds are checked before trimming, leaving an empty name.
        empty_profile = await req("PATCH", "/api/staff/me", {"first_name": ""}, token=teacher["access_token"])
        put("empty_profile_name", empty_name_update_status=empty_profile.status_code,
            stored_first_name_is_empty=empty_profile.json().get("first_name") == "")

        # Concurrent bad passwords must not lose failed-attempt increments.
        race_bad = await asyncio.gather(*[req("POST", "/api/auth/login", {"email": users["login-race"]["email"], "password": "wrong", "device_id": f"bad-race-{i}"}, ip=f"198.51.100.{100+i}") for i in range(6)])
        async with AsyncSessionLocal() as db:
            raced_user = await db.get(Staff, uuid.UUID(users["login-race"]["id"]))
            put("concurrent_failed_login_accounting", bad_password_responses=[r.status_code for r in race_bad],
                stored_failed_attempts=raced_user.login_attempts, stored_lockouts=raced_user.lockout_count,
                temporarily_locked=bool(raced_user.locked_until), permanently_locked=raced_user.permanent_lock)

        # DEBUG_OTP=false omits response delivery but codes are still only printed.
        settings.DEBUG_OTP = False
        r = await req("POST", "/api/auth/forgot-password", {"email": users["admin"]["email"]}, ip="192.0.2.90")
        put("non_debug_reset_delivery", response_status=r.status_code, debug_otp_present=bool(r.json().get("debug_otp")),
            note="Source has print() only; no mail transport is invoked.")

    await async_engine.dispose()
    output = ROOT / "audit" / "security-auth-results.json"
    output.write_text(json.dumps({"database": DB.name, "isolation": "fresh audit-only DB", "results": results}, indent=2), encoding="utf-8")


if __name__ == "__main__":
    with contextlib.redirect_stdout(io.StringIO()):
        asyncio.run(main())
    print(json.dumps(results, indent=2))
