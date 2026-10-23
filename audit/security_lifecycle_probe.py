"""Cover every auth/staff/school operation using a fresh audit-only database.

Reuses security_auth_probe's isolated fixture setup, never its main probe or
any shared server/database. No credentials, OTPs, or tokens are saved to output.
"""
import asyncio
import base64
import contextlib
import io
import json
from urllib.parse import urlsplit
import security_auth_probe as fixtures

ROOT = fixtures.ROOT
PASSWORD = fixtures.PASSWORD
NEW_PASSWORD = fixtures.NEW_PASSWORD
results = []
coverage = {}
operations = {}
for route in fixtures.app.routes:
    endpoint = getattr(route, "endpoint", None)
    if getattr(endpoint, "__module__", "") in {
        "app.api.routers.auth", "app.api.routers.staff", "app.api.routers.schools"
    }:
        for method in route.methods:
            operations[f"{method} {route.path}"] = route


async def main():
    await fixtures.setup()
    transport = fixtures.httpx.ASGITransport(app=fixtures.app, raise_app_exceptions=False)
    async with fixtures.httpx.AsyncClient(transport=transport, base_url="http://audit.local") as client:
        sequence = 0
        async def req(name, method, path, data=None, token=None, expected=None, **kwargs):
            nonlocal sequence
            sequence += 1
            # Isolate ordinary lifecycle verification from the known broken IP
            # limiter. Dedicated abuse probes already document that separate bug.
            headers = {"X-Forwarded-For": f"198.51.{sequence // 250}.{sequence % 250 + 1}"}
            if token:
                headers["Authorization"] = "Bearer " + token
            response = await client.request(method, path, json=data, headers=headers, **kwargs)
            operation = None
            for key, route in operations.items():
                if method in route.methods and route.path_regex.fullmatch(urlsplit(path).path):
                    operation = key
                    coverage.setdefault(key, []).append(response.status_code)
                    break
            item = {"probe": name, "operation": operation, "status": response.status_code}
            if expected is not None:
                item["expected"] = expected
                item["matches_expected"] = response.status_code in (expected if isinstance(expected, list) else [expected])
            results.append(item)
            return response

        async def sign_in(name, email, password=PASSWORD, expected=201, device="lifecycle-device"):
            response = await req(name + ": login", "POST", "/api/auth/login", {"email": email, "password": password, "device_id": device}, expected=expected)
            data = response.json()
            if response.status_code == 201 and data.get("requires_2fa"):
                response = await req(name + ": OTP", "POST", "/api/auth/verify-otp", {"user_id": data["user_id"], "code": data["debug_otp"], "device_id": device}, expected=201)
                data = response.json()
            return data

        admin = await sign_in("fixture principal", fixtures.users["admin"]["email"])
        teacher = await sign_in("fixture teacher", fixtures.users["teacher"]["email"])
        operator = await sign_in("fixture operator", fixtures.users["operator"]["email"])
        a, t, o = (account["access_token"] for account in (admin, teacher, operator))
        admin_id = fixtures.users["admin"]["id"]
        other_id = fixtures.users["other"]["id"]
        own_school = fixtures.users["admin"]["school_id"]

        # A teacher joins an existing school, remains pending, and is approved.
        registration = {"school_id": own_school, "email": "lifecycle-teacher@example.com", "password": PASSWORD,
                        "first_name": "Lifecycle", "last_name": "Teacher", "contact_number": "09170000000", "employee_no": "LIFE-001"}
        response = await req("register teacher", "POST", "/api/auth/register", registration, expected=201)
        joined_id = response.json()["user_id"]
        await sign_in("pending teacher denied", registration["email"], expected=403)
        pending = await req("principal pending queue", "GET", "/api/staff/pending", token=a, expected=200)
        results.append({"probe": "registered teacher appears pending", "present": any(row["id"] == joined_id for row in pending.json())})
        await req("teacher cannot read pending queue", "GET", "/api/staff/pending", token=t, expected=403)
        await req("approve teacher", "PATCH", f"/api/staff/{joined_id}", {"action": "approve", "role": "teacher"}, a, expected=200)
        joined = await sign_in("approved teacher signs in", registration["email"])
        j = joined["access_token"]
        await req("staff directory", "GET", "/api/staff", token=a, expected=200)
        await req("teachers directory", "GET", "/api/staff/teachers", token=t, expected=200)
        await req("principal reads colleague", "GET", f"/api/staff/{joined_id}", token=a, expected=200)
        await req("cross-school colleague hidden", "GET", f"/api/staff/{other_id}", token=a, expected=404)
        await req("self profile", "GET", "/api/staff/me", token=j, expected=200)
        await req("edit self profile", "PATCH", "/api/staff/me", {"first_name": "Updated", "contact_number": "09170000001"}, j, expected=200)
        await req("auth identity", "GET", "/api/auth/me", token=j, expected=200)

        # Valid image creation/deletion, including filesystem and stale-URL check.
        png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aL1sAAAAASUVORK5CYII=")
        response = await req("upload avatar", "POST", "/api/staff/me/photo", token=j, expected=200, files={"file": ("audit.png", png, "image/png")})
        photo = response.json()["profile_pic"]
        before_photo = await client.get(photo)
        deleted_photo = await req("remove avatar", "DELETE", "/api/staff/me/photo", token=j, expected=200)
        after_photo = await client.get(photo)
        results.append({"probe": "avatar deletion removes saved file", "before_status": before_photo.status_code, "after_status": after_photo.status_code, "profile_cleared": deleted_photo.json()["profile_pic"] is None})
        await req("remove absent avatar is idempotent", "DELETE", "/api/staff/me/photo", token=j, expected=200)

        # Normal password change and the expected invalid-input branches.
        await req("wrong current password rejected", "POST", "/api/staff/me/password", {"current_password": "wrong", "new_password": NEW_PASSWORD}, j, expected=400)
        await req("same password rejected", "POST", "/api/staff/me/password", {"current_password": PASSWORD, "new_password": PASSWORD}, j, expected=400)
        await req("weak changed password rejected", "POST", "/api/staff/me/password", {"current_password": PASSWORD, "new_password": "aaaaaaaa"}, j, expected=422)
        await req("change own password", "POST", "/api/staff/me/password", {"current_password": PASSWORD, "new_password": NEW_PASSWORD}, j, expected=200)
        await sign_in("old changed password denied", registration["email"], PASSWORD, expected=401)
        joined = await sign_in("new changed password accepted", registration["email"], NEW_PASSWORD)
        j = joined["access_token"]

        # Self-preservation and role/tenant protections.
        for action in ("reject", "deactivate"):
            await req("principal self " + action + " prevented", "PATCH", f"/api/staff/{admin_id}", {"action": action}, a, expected=400)
        await req("principal self delete prevented", "DELETE", f"/api/staff/{admin_id}", token=a, expected=400)
        await req("teacher cannot delete principal", "DELETE", f"/api/staff/{admin_id}", token=t, expected=403)
        await req("cross-school delete hidden", "DELETE", f"/api/staff/{other_id}", token=a, expected=404)
        await req("teacher cannot administer account", "PATCH", f"/api/staff/{joined_id}", {"action": "unlock"}, t, expected=403)
        await req("operator cannot use school staff queue", "GET", "/api/staff/pending", token=o, expected=403)
        await req("principal audit log", "GET", "/api/staff/audit-logs", token=a, expected=200)
        await req("teacher cannot read audit log", "GET", "/api/staff/audit-logs", token=t, expected=403)

        await req("deactivate teacher", "PATCH", f"/api/staff/{joined_id}", {"action": "deactivate", "reason": "Fixture lifecycle"}, a, expected=200)
        await sign_in("deactivated teacher login denied", registration["email"], NEW_PASSWORD, expected=403)
        await req("deactivated teacher token denied", "GET", "/api/auth/me", token=j, expected=403)
        await req("reactivate teacher", "PATCH", f"/api/staff/{joined_id}", {"action": "reactivate"}, a, expected=200)
        await sign_in("reactivated teacher login", registration["email"], NEW_PASSWORD)
        await req("unlock teacher", "PATCH", f"/api/staff/{joined_id}", {"action": "unlock"}, a, expected=200)

        second = {**registration, "email": "lifecycle-rejected@example.com", "employee_no": "LIFE-002"}
        second_response = await req("register second teacher", "POST", "/api/auth/register", second, expected=201)
        rejected_id = second_response.json()["user_id"]
        await req("reject teacher", "PATCH", f"/api/staff/{rejected_id}", {"action": "reject", "reason": "Fixture rejection"}, a, expected=200)
        await sign_in("rejected teacher denied", second["email"], expected=403)
        duplicate_employee = {**registration, "email": "lifecycle-duplicate-employee@example.com"}
        duplicate_employee_response = await req("duplicate employee number", "POST", "/api/auth/register", duplicate_employee, expected=409)
        results.append({"probe": "duplicate employee number diagnostic", "error_type": duplicate_employee_response.json().get("error_type")})

        # Operator approves a new school and gates all accounts when suspended.
        school_request = {"school": {"name": "Lifecycle New School", "city": "Audit City", "school_type": "public"},
                          "first_name": "Lifecycle", "last_name": "Principal", "email": "lifecycle-principal@example.com", "password": PASSWORD, "contact_number": "09170000000"}
        response = await req("register school and principal", "POST", "/api/schools/register", school_request, expected=201)
        school_id = response.json()["school_id"]
        await sign_in("pending principal denied", school_request["email"], expected=403)
        listing = await req("operator pending schools", "GET", "/api/schools?status=pending", token=o, expected=200)
        results.append({"probe": "pending school visible to operator", "present": any(row["school_id"] == school_id for row in listing.json())})
        directory = await req("public approved directory", "GET", "/api/schools/directory", expected=200)
        results.append({"probe": "pending school excluded from public directory", "excluded": all(row["school_id"] != school_id for row in directory.json())})
        await req("teacher cannot list schools as operator", "GET", "/api/schools", token=t, expected=403)
        await req("principal cannot decide school", "PATCH", f"/api/schools/{school_id}", {"action": "approve"}, a, expected=403)
        await req("reject school requires explanation", "PATCH", f"/api/schools/{school_id}", {"action": "reject"}, o, expected=400)
        await req("operator rejects school", "PATCH", f"/api/schools/{school_id}", {"action": "reject", "reason": "Fixture missing verification"}, o, expected=200)
        await sign_in("rejected principal denied", school_request["email"], expected=403)
        await req("operator approves school", "PATCH", f"/api/schools/{school_id}", {"action": "approve"}, o, expected=200)
        principal = await sign_in("approved principal signs in", school_request["email"])
        p = principal["access_token"]
        directory = await req("approved school joins directory", "GET", "/api/schools/directory?search=Lifecycle", expected=200)
        results.append({"probe": "approved school directory presence", "present": any(row["school_id"] == school_id for row in directory.json())})
        await req("principal own school settings", "GET", "/api/schools/me", token=p, expected=200)
        await req("teacher own school settings", "GET", "/api/schools/me", token=t, expected=200)
        await req("operator has no own school", "GET", "/api/schools/me", token=o, expected=404)
        await req("principal edits school settings", "PATCH", "/api/schools/me", {"short_name": "LNS", "city": "Updated Audit City", "passing_mark": 76}, p, expected=200)
        await req("teacher cannot edit settings", "PATCH", "/api/schools/me", {"short_name": "Forbidden"}, t, expected=403)
        await req("operator cannot edit school through mine", "PATCH", "/api/schools/me", {"short_name": "Forbidden"}, o, expected=403)
        await req("operator suspends school", "PATCH", f"/api/schools/{school_id}", {"action": "deactivate", "reason": "Fixture suspension"}, o, expected=200)
        await sign_in("suspended principal login denied", school_request["email"], expected=403)
        await req("suspended principal existing token denied", "GET", "/api/schools/me", token=p, expected=403)
        directory = await req("suspended school directory filter", "GET", "/api/schools/directory", expected=200)
        results.append({"probe": "suspended school excluded", "excluded": all(row["school_id"] != school_id for row in directory.json())})
        await req("operator restores school", "PATCH", f"/api/schools/{school_id}", {"action": "reactivate"}, o, expected=200)
        await sign_in("restored principal login", school_request["email"])
        await req("restored school usable", "GET", "/api/schools/me", token=p, expected=200)
        await req("duplicate school registration conflicts", "POST", "/api/schools/register", {**school_request, "email": "lifecycle-other-principal@example.com", "school": {**school_request["school"], "city": "Updated Audit City"}}, expected=409)

        # Complete reset/refresh/logout path on the joined disposable account.
        response = await req("request password reset", "POST", "/api/auth/forgot-password", {"email": registration["email"]}, expected=202)
        reset_code = response.json()["debug_otp"]
        await req("wrong reset code rejected", "POST", "/api/auth/verify-reset-otp", {"email": registration["email"], "code": "111111" if reset_code != "111111" else "222222"}, expected=400)
        reset_exchange = await req("exchange reset code", "POST", "/api/auth/verify-reset-otp", {"email": registration["email"], "code": reset_code}, expected=200)
        reset_password = "Lifecycle-Recovered-Password3!"
        reset_body = {"token": reset_exchange.json()["token"], "new_password": reset_password, "confirm_password": reset_password}
        await req("complete reset", "POST", "/api/auth/reset-password", reset_body, expected=200)
        await req("used reset token rejected", "POST", "/api/auth/reset-password", reset_body, expected=400)
        joined = await sign_in("reset password login", registration["email"], reset_password)
        j = joined["access_token"]
        await req("refresh session", "POST", "/api/auth/refresh", token=joined["refresh_token"], expected=200)
        await req("logout session", "POST", "/api/auth/logout", token=j, expected=204)
        await req("delete staff", "DELETE", f"/api/staff/{joined_id}", token=a, expected=204)
        await req("deleted account token denied", "GET", "/api/auth/me", token=j, expected=401)
        await req("deleted colleague no longer found", "GET", f"/api/staff/{joined_id}", token=a, expected=404)

        # Generic unauthenticated/invalid-input cases for every public/protected operation.
        anonymous_cases = [
            ("GET", "/api/auth/me", None), ("POST", "/api/auth/logout", None),
            ("POST", "/api/auth/refresh", None), ("GET", "/api/staff/me", None),
            ("PATCH", "/api/staff/me", {"first_name": "Audit"}),
            ("POST", "/api/staff/me/password", {"current_password": PASSWORD, "new_password": NEW_PASSWORD}),
            ("DELETE", "/api/staff/me/photo", None), ("GET", "/api/staff", None),
            ("GET", "/api/staff/teachers", None), ("GET", "/api/staff/pending", None),
            ("GET", "/api/staff/audit-logs", None), ("GET", f"/api/staff/{admin_id}", None),
            ("PATCH", f"/api/staff/{admin_id}", {"action": "approve"}), ("DELETE", f"/api/staff/{admin_id}", None),
            ("GET", "/api/schools/me", None), ("PATCH", "/api/schools/me", {"short_name": "Audit"}),
            ("GET", "/api/schools", None), ("PATCH", f"/api/schools/{school_id}", {"action": "approve"}),
        ]
        for method, path, body in anonymous_cases:
            await req("unauthenticated " + method + " " + path, method, path, body, expected=401)
        await req("unauthenticated photo upload", "POST", "/api/staff/me/photo", expected=401, files={"file": ("audit.png", png, "image/png")})
        await req("invalid teacher registration rejected", "POST", "/api/auth/register", {}, expected=422)
        await req("invalid forgot email rejected", "POST", "/api/auth/forgot-password", {"email": "invalid"}, expected=422)
        await req("unknown reset token rejected", "POST", "/api/auth/reset-password", {"token": "invalid", "new_password": NEW_PASSWORD, "confirm_password": NEW_PASSWORD}, expected=400)
        await req("wrong login OTP rejected", "POST", "/api/auth/verify-otp", {"user_id": fixtures.users["victim"]["id"], "code": "000000", "device_id": "bad-device"}, expected=400)
        await req("invalid school registration rejected", "POST", "/api/schools/register", {}, expected=422)
        await req("overlong directory search rejected", "GET", "/api/schools/directory?search=" + "x" * 101, expected=422)

    await fixtures.async_engine.dispose()
    payload = {"isolation": "fresh invented-data fixture DB; no shared server or existing DB used", "database": fixtures.DB.name,
               "declared_operation_count": len(operations), "invoked_operation_count": len(coverage),
               "uninvoked_operations": sorted(set(operations) - set(coverage)), "coverage": coverage, "probes": results,
               "unexpected_statuses": [r for r in results if r.get("matches_expected") is False]}
    (ROOT / "audit" / "security-lifecycle-results.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


if __name__ == "__main__":
    with contextlib.redirect_stdout(io.StringIO()):
        payload = asyncio.run(main())
    print(json.dumps({k: payload[k] for k in ("declared_operation_count", "invoked_operation_count", "uninvoked_operations", "unexpected_statuses")}, indent=2))
