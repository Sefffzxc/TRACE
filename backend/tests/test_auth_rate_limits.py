"""School-network authentication throttles, without DB/email/provider traffic.

Run from backend: python -m unittest discover -s tests -p test_auth_rate_limits.py
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import Depends, FastAPI, HTTPException, Request
import httpx

from app.api import deps
from app.core.config import settings


class AuthenticationRateLimitTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.now = 1000.0
        clock = patch.object(deps, "time", SimpleNamespace(monotonic=lambda: self.now))
        clock.start()
        self.addCleanup(clock.stop)
        config = patch.multiple(
            settings, AUTH_LOGIN_IP_LIMIT=60, AUTH_OTP_IP_LIMIT=60,
            AUTH_REGISTER_IP_LIMIT=60, AUTH_RESET_IP_LIMIT=60,
        )
        config.start()
        self.addCleanup(config.stop)
        deps._BUCKETS.clear()
        self.addCleanup(deps._BUCKETS.clear)

        application = FastAPI()

        async def accepted(request: Request):
            # Reading the body here confirms the dependency did not consume it.
            return {"accepted": True, "payload": await request.json()}

        self.routes = {
            "/api/auth/login": deps.limit_login,
            "/api/auth/verify-otp": deps.limit_otp,
            "/api/auth/verify-reset-otp": deps.limit_otp,
            "/api/auth/register": deps.limit_register,
            "/api/schools/register": deps.limit_register,
            "/api/auth/forgot-password": deps.limit_password_reset,
        }
        for path, dependency in self.routes.items():
            application.add_api_route(
                path, accepted, methods=["POST"], dependencies=[Depends(dependency)],
            )
        self.application = application
        self.client = self.make_client("198.51.100.42")
        self.addAsyncCleanup(self.client.aclose)

    def make_client(self, ip):
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.application, client=(ip, 12345)),
            base_url="http://testserver",
        )

    async def test_thirteen_users_can_register_login_and_verify_together(self):
        """The reported group size passes each phase on one school Wi-Fi IP."""
        for path in self.routes:
            with self.subTest(path=path):
                deps._BUCKETS.clear()
                payloads = [
                    {"email": f"teacher{number}@example.com", "user_id": str(uuid4()), "code": "123456"}
                    for number in range(13)
                ]
                responses = await asyncio.gather(*[
                    self.client.post(path, json=payload) for payload in payloads
                ])
                self.assertEqual([response.status_code for response in responses], [200] * 13)
                self.assertEqual(responses[0].json()["payload"], payloads[0])

    async def test_original_per_account_limits_apply_across_different_ips(self):
        limits = {
            "/api/auth/login": 10, "/api/auth/verify-otp": 10,
            "/api/auth/verify-reset-otp": 10, "/api/auth/register": 5,
            "/api/schools/register": 5, "/api/auth/forgot-password": 5,
        }
        for path, limit in limits.items():
            with self.subTest(path=path):
                deps._BUCKETS.clear()
                payload = {"email": "one@example.com", "user_id": str(uuid4())}
                for _ in range(limit):
                    self.assertEqual((await self.client.post(path, json=payload)).status_code, 200)
                async with self.make_client("203.0.113.21") as other_network:
                    blocked = await other_network.post(path, json=payload)
                self.assertEqual(blocked.status_code, 429)
                self.assertEqual(blocked.headers["Retry-After"], "60" if limit == 10 else "300")

    async def test_email_case_whitespace_and_extra_fields_cannot_rotate_account(self):
        for number in range(10):
            response = await self.client.post("/api/auth/login", json={
                "email": "One@Example.com" if number % 2 else " one@example.com ",
                "user_id": str(uuid4()),
            })
            self.assertEqual(response.status_code, 200)
        self.assertEqual((await self.client.post("/api/auth/login", json={
            "email": "ONE@EXAMPLE.COM", "user_id": str(uuid4()),
        })).status_code, 429)
        self.assertFalse(any("@" in key for key in deps._BUCKETS))

    async def test_otp_extra_email_cannot_rotate_user_identity(self):
        identifier = str(uuid4())
        for number in range(10):
            response = await self.client.post("/api/auth/verify-otp", json={
                "user_id": identifier.upper() if number % 2 else identifier,
                "email": f"ignored{number}@example.com",
            })
            self.assertEqual(response.status_code, 200)
        response = await self.client.post("/api/auth/verify-otp", json={
            "user_id": identifier, "email": "different@example.com",
        })
        self.assertEqual(response.status_code, 429)

    async def test_shared_ip_ceiling_ignores_spoofed_forwarded_headers(self):
        with patch.object(settings, "AUTH_LOGIN_IP_LIMIT", 13):
            for number in range(13):
                response = await self.client.post("/api/auth/login", json={
                    "email": f"different{number}@example.com",
                }, headers={"X-Forwarded-For": f"192.0.2.{number}", "X-Real-IP": f"192.0.2.{number}"})
                self.assertEqual(response.status_code, 200)
            blocked = await self.client.post("/api/auth/login", json={"email": "next@example.com"}, headers={
                "X-Forwarded-For": "203.0.113.99", "X-Real-IP": "203.0.113.99",
            })
            self.assertEqual(blocked.status_code, 429)
            async with self.make_client("203.0.113.45") as different_network:
                allowed = await different_network.post("/api/auth/login", json={"email": "next@example.com"})
            self.assertEqual(allowed.status_code, 200)

    async def test_missing_or_malformed_identity_still_uses_ip_ceiling(self):
        with patch.object(settings, "AUTH_LOGIN_IP_LIMIT", 3):
            for payload in ({}, {"email": None}, ["not an object"]):
                self.assertEqual((await self.client.post("/api/auth/login", json=payload)).status_code, 200)
            self.assertEqual((await self.client.post("/api/auth/login", json={})).status_code, 429)

    async def test_retry_after_expires_at_exact_boundary_without_retry_extension(self):
        payload = {"email": "same@example.com"}
        for _ in range(10):
            self.assertEqual((await self.client.post("/api/auth/login", json=payload)).status_code, 200)
        initial = await self.client.post("/api/auth/login", json=payload)
        self.assertEqual(initial.headers["Retry-After"], "60")
        self.now += 59.01
        almost = await self.client.post("/api/auth/login", json=payload)
        self.assertEqual(almost.status_code, 429)
        self.assertEqual(almost.headers["Retry-After"], "1")
        self.now = 1060.0
        self.assertEqual((await self.client.post("/api/auth/login", json=payload)).status_code, 200)

    async def test_rejected_account_attempts_do_not_consume_other_users_ip_slots(self):
        with patch.object(settings, "AUTH_LOGIN_IP_LIMIT", 13):
            for _ in range(10):
                self.assertEqual((await self.client.post("/api/auth/login", json={"email": "same@example.com"})).status_code, 200)
            for _ in range(10):
                self.assertEqual((await self.client.post("/api/auth/login", json={"email": "same@example.com"})).status_code, 429)
            for number in range(3):
                self.assertEqual((await self.client.post("/api/auth/login", json={"email": f"other{number}@example.com"})).status_code, 200)


class SlidingWindowStorageTests(unittest.TestCase):
    def setUp(self):
        deps._BUCKETS.clear()
        self.addCleanup(deps._BUCKETS.clear)

    def test_concurrent_threads_cannot_overbook_one_window(self):
        def attempt(_):
            try:
                deps.rate_limit("concurrent", 13, 60)
                return True
            except HTTPException as error:
                self.assertEqual(error.status_code, 429)
                return False

        with ThreadPoolExecutor(max_workers=20) as pool:
            results = list(pool.map(attempt, range(100)))
        self.assertEqual(sum(results), 13)

    def test_bounded_storage_keeps_active_counters_then_reclaims_expired_ones(self):
        now = [1000.0]
        with patch.object(deps, "_MAX_RATE_BUCKETS", 3), patch.object(
            deps, "time", SimpleNamespace(monotonic=lambda: now[0]),
        ):
            for key in ("one", "two", "three"):
                deps.rate_limit(key, 1, 60)
            with self.assertRaises(HTTPException) as blocked:
                deps.rate_limit("four", 1, 60)
            self.assertEqual(blocked.exception.headers["Retry-After"], "60")
            self.assertEqual(set(deps._BUCKETS), {"one", "two", "three"})
            with self.assertRaises(HTTPException):
                deps.rate_limit("one", 1, 60)
            now[0] = 1060.0
            deps.rate_limit("four", 1, 60)
            self.assertEqual(set(deps._BUCKETS), {"four"})

    def test_two_new_identities_reclaim_stale_storage_below_the_capacity(self):
        # With one unused slot, a new IP + account needs two slots. Stale
        # counters must be reclaimed even though storage is not completely full.
        now = [1000.0]
        with patch.object(deps, "_MAX_RATE_BUCKETS", 3), patch.object(
            deps, "time", SimpleNamespace(monotonic=lambda: now[0]),
        ):
            deps.rate_limit("old-ip", 1, 60)
            deps.rate_limit("old-account", 1, 60)
            now[0] = 1061.0
            deps._rate_limit_many([("new-ip", 1, 60), ("new-account", 1, 60)])
            self.assertEqual(set(deps._BUCKETS), {"new-ip", "new-account"})
            self.assertTrue(all(list(bucket.timestamps) == [1061.0] for bucket in deps._BUCKETS.values()))

    def test_two_new_identities_cannot_evict_live_counters_below_the_capacity(self):
        now = [1000.0]
        with patch.object(deps, "_MAX_RATE_BUCKETS", 3), patch.object(
            deps, "time", SimpleNamespace(monotonic=lambda: now[0]),
        ):
            deps.rate_limit("live-ip", 1, 60)
            deps.rate_limit("live-account", 1, 60)
            now[0] = 1010.0
            with self.assertRaises(HTTPException) as blocked:
                deps._rate_limit_many([("new-ip", 1, 60), ("new-account", 1, 60)])
            self.assertEqual(blocked.exception.headers["Retry-After"], "50")
            self.assertEqual(set(deps._BUCKETS), {"live-ip", "live-account"})
            self.assertTrue(all(list(bucket.timestamps) == [1000.0] for bucket in deps._BUCKETS.values()))
            now[0] = 1060.0
            deps._rate_limit_many([("new-ip", 1, 60), ("new-account", 1, 60)])
            self.assertEqual(set(deps._BUCKETS), {"new-ip", "new-account"})

if __name__ == "__main__":
    unittest.main()
