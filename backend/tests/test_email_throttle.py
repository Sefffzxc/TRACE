"""Concurrent email delivery and safe provider retry regressions.

All HTTP uses MockTransport; no provider credentials or real email traffic.
Run from backend: python -m unittest discover -s tests -p test_email_throttle.py
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
import io
import logging
import os
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from app.core.config import settings
from app.services import email as email_service


HTTPClient = httpx.AsyncClient
CODE = "483927"
PRIVATE_KEY = "private-unit-test-email-key"


class EmailBurstTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        configuration = patch.multiple(
            settings, EMAIL_ENABLED=True, RESEND_API_KEY=PRIVATE_KEY,
            EMAIL_FROM="TRACE <noreply@example.com>", EMAIL_TIMEOUT_SECONDS=3.0,
            EMAIL_SEND_RATE_PER_SECOND=2.0, EMAIL_QUEUE_TIMEOUT_SECONDS=20.0,
            DEBUG_OTP=False, LOG_OTP_CODES=False,
        )
        configuration.start()
        self.addCleanup(configuration.stop)
        scheduler = patch.object(email_service, "_EMAIL_PACER", email_service._EmailPacer())
        scheduler.start()
        self.addCleanup(scheduler.stop)
        self.output = io.StringIO()
        handler = logging.StreamHandler(self.output)
        email_service.log.addHandler(handler)
        self.addCleanup(email_service.log.removeHandler, handler)

    def mock_provider(self, handler):
        factory = patch.object(
            email_service.httpx, "AsyncClient", side_effect=lambda **kwargs:
                HTTPClient(transport=httpx.MockTransport(handler), **kwargs),
        )
        factory.start()
        self.addCleanup(factory.stop)

    async def send(self, address="teacher@example.com"):
        await email_service.send_otp_email(address, CODE, purpose="login")

    async def test_thirteen_otp_requests_are_paced_and_network_posts_can_overlap(self):
        starts = []
        active = 0
        peak_active = 0

        async def provider(request):
            nonlocal active, peak_active
            starts.append(time.monotonic())
            active += 1
            peak_active = max(peak_active, active)
            # A slow provider request must not hold the scheduler's lock.
            await asyncio.sleep(0.6)
            active -= 1
            return httpx.Response(200, json={"id": f"email-{len(starts)}"})

        self.mock_provider(provider)
        await asyncio.gather(*[self.send(f"teacher{number}@example.com") for number in range(13)])
        self.assertEqual(len(starts), 13)
        self.assertGreaterEqual(peak_active, 2)
        self.assertTrue(all(later - earlier >= 0.49 for earlier, later in zip(starts, starts[1:])))
        self.assertGreaterEqual(starts[-1] - starts[0], 5.99)
        self.assertLess(starts[-1] - starts[0], settings.EMAIL_QUEUE_TIMEOUT_SECONDS)
        self.assertNotIn(CODE, self.output.getvalue())
        self.assertNotIn(PRIVATE_KEY, self.output.getvalue())

    async def test_rate_rejection_retries_same_message_after_provider_wait(self):
        starts = []
        payloads = []

        def provider(request):
            starts.append(time.monotonic())
            payloads.append(request.content)
            if len(starts) == 1:
                return httpx.Response(429, headers={"Retry-After": "0.08"}, json={
                    "name": "rate_limit_exceeded", "message": f"private {CODE} {PRIVATE_KEY}",
                })
            return httpx.Response(200, json={"id": "accepted-after-wait"})

        self.mock_provider(provider)
        with patch.object(settings, "EMAIL_SEND_RATE_PER_SECOND", 100.0):
            await self.send()
        self.assertEqual(len(starts), 2)
        self.assertGreaterEqual(starts[1] - starts[0], 0.075)
        self.assertEqual(payloads[0], payloads[1])
        self.assertNotIn(CODE, self.output.getvalue())
        self.assertNotIn(PRIVATE_KEY, self.output.getvalue())

    async def test_provider_cooldown_also_delays_other_waiting_messages(self):
        starts = []
        first_rejection = asyncio.Event()

        async def provider(request):
            starts.append(time.monotonic())
            if len(starts) == 1:
                first_rejection.set()
                return httpx.Response(429, headers={"Retry-After": "0.1"}, json={"name": "rate_limit_exceeded"})
            return httpx.Response(200, json={"id": f"accepted-{len(starts)}"})

        self.mock_provider(provider)
        with patch.object(settings, "EMAIL_SEND_RATE_PER_SECOND", 100.0):
            first = asyncio.create_task(self.send("first@example.com"))
            await first_rejection.wait()
            second = asyncio.create_task(self.send("second@example.com"))
            await asyncio.gather(first, second)
        self.assertEqual(len(starts), 3)
        self.assertGreaterEqual(starts[1] - starts[0], 0.095)

    async def test_rate_retries_are_bounded_to_two_additional_attempts(self):
        requests = []

        def provider(request):
            requests.append(request)
            return httpx.Response(429, headers={"Retry-After": "0"}, json={"name": "rate_limit_exceeded"})

        self.mock_provider(provider)
        with patch.object(settings, "EMAIL_SEND_RATE_PER_SECOND", 100.0):
            with self.assertRaises(email_service.EmailDeliveryError):
                await self.send()
        self.assertEqual(len(requests), 3)

    async def test_retry_after_longer_than_queue_deadline_fails_without_waiting(self):
        requests = []

        def provider(request):
            requests.append(request)
            return httpx.Response(429, headers={"Retry-After": "30"}, json={"name": "rate_limit_exceeded"})

        self.mock_provider(provider)
        started = time.monotonic()
        with self.assertRaises(email_service.EmailDeliveryError):
            await self.send()
        self.assertEqual(len(requests), 1)
        self.assertLess(time.monotonic() - started, 1)

    async def test_full_queue_fails_before_posting_additional_email(self):
        requests = []

        def provider(request):
            requests.append(request)
            return httpx.Response(200, json={"id": "accepted"})

        self.mock_provider(provider)
        with patch.object(settings, "EMAIL_QUEUE_TIMEOUT_SECONDS", 0.01):
            await self.send("first@example.com")
            with self.assertRaises(email_service.EmailDeliveryError):
                await self.send("second@example.com")
        self.assertEqual(len(requests), 1)

    async def test_daily_monthly_unknown_and_malformed_429_are_not_retried(self):
        for body in ({"name": "daily_quota_exceeded"}, {"name": "monthly_quota_exceeded"},
                     {"name": f"private {CODE} {PRIVATE_KEY}"}, [], None):
            with self.subTest(body=body):
                requests = []

                def provider(request):
                    requests.append(request)
                    return httpx.Response(429, json=body) if body is not None else httpx.Response(429, text="not JSON")

                with patch.object(email_service, "_EMAIL_PACER", email_service._EmailPacer()), patch.object(
                    email_service.httpx, "AsyncClient", side_effect=lambda **kwargs:
                        HTTPClient(transport=httpx.MockTransport(provider), **kwargs),
                ):
                    with self.assertRaises(email_service.EmailDeliveryError):
                        await self.send()
                self.assertEqual(len(requests), 1)
        self.assertNotIn(CODE, self.output.getvalue())
        self.assertNotIn(PRIVATE_KEY, self.output.getvalue())

    async def test_transport_timeout_is_not_retried_and_is_redacted(self):
        requests = []

        def provider(request):
            requests.append(request)
            raise httpx.ReadTimeout(f"private {CODE} {PRIVATE_KEY}", request=request)

        self.mock_provider(provider)
        with self.assertRaises(email_service.EmailDeliveryError) as error:
            await self.send()
        self.assertEqual(len(requests), 1)
        self.assertNotIn(CODE, self.output.getvalue() + str(error.exception))
        self.assertNotIn(PRIVATE_KEY, self.output.getvalue() + str(error.exception))


class RetryHeaderAndLoopTests(unittest.TestCase):
    def test_retry_after_http_date_and_invalid_headers(self):
        future = datetime.now(timezone.utc) + timedelta(seconds=5)
        date = httpx.Response(429, headers={"Retry-After": format_datetime(future, usegmt=True)})
        self.assertGreater(email_service._retry_after_seconds(date, 1), 3)
        self.assertLessEqual(email_service._retry_after_seconds(date, 1), 5)
        for value in ("", "invalid", "nan", "inf"):
            response = httpx.Response(429, headers={"Retry-After": value})
            self.assertEqual(email_service._retry_after_seconds(response, 1.0), 1.0)

    def test_process_scheduler_can_be_shared_by_different_event_loops(self):
        scheduler = email_service._EmailPacer()

        def claim(_):
            asyncio.run(scheduler.wait_turn(time.monotonic() + 2))
            return time.monotonic()

        with patch.object(settings, "EMAIL_SEND_RATE_PER_SECOND", 100.0):
            with ThreadPoolExecutor(max_workers=2) as pool:
                starts = sorted(pool.map(claim, range(4)))
        self.assertEqual(len(starts), 4)
        self.assertGreaterEqual(starts[-1] - starts[0], 0.025)


if __name__ == "__main__":
    unittest.main()
