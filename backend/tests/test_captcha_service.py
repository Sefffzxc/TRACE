"""Turnstile verification with mocked HTTP; no real provider or database calls.

Run from backend: python -m unittest discover -s tests -p test_captcha_service.py
"""
from __future__ import annotations

from contextlib import contextmanager, redirect_stdout
import io
import logging
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs

from fastapi import HTTPException
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services import captcha


TOKEN = "private-captcha-unit-test-token"
SECRET = "private-captcha-unit-test-secret"


@contextmanager
def captured_logs():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    root = logging.getLogger()
    previous_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    try:
        with redirect_stdout(stream):
            yield stream
    finally:
        root.removeHandler(handler)
        root.setLevel(previous_level)
        handler.close()


class CaptchaServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.settings = SimpleNamespace(
            CAPTCHA_ENABLED=True,
            TURNSTILE_SITE_KEY="public-unit-test-site-key",
            TURNSTILE_SECRET_KEY=SECRET,
            TURNSTILE_ALLOWED_HOSTNAMES="traceedu.app, www.traceedu.app",
            CAPTCHA_TIMEOUT_SECONDS=3.0,
        )
        configuration = patch.object(captcha, "settings", self.settings)
        configuration.start()
        self.addCleanup(configuration.stop)

    async def verify_reply(self, reply, *, action="login", status=200):
        def respond(request):
            return httpx.Response(status, json=reply)

        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        with patch.object(captcha.httpx, "AsyncClient", return_value=client):
            await captcha.verify_captcha(TOKEN, action=action)

    def assert_failure(self, error, *, status, code):
        self.assertEqual(error.status_code, status)
        self.assertEqual(error.detail["code"], code)
        self.assertIs(error.detail["captcha_required"], True)
        self.assertTrue(error.detail["message"])
        self.assertNotIn(TOKEN, str(error.detail))
        self.assertNotIn(SECRET, str(error.detail))

    async def test_disabled_does_not_require_config_token_or_provider(self):
        self.settings.CAPTCHA_ENABLED = False
        self.settings.TURNSTILE_SECRET_KEY = ""
        self.settings.TURNSTILE_SITE_KEY = ""
        self.settings.TURNSTILE_ALLOWED_HOSTNAMES = ""
        with patch.object(captcha.httpx, "AsyncClient") as factory:
            self.assertIsNone(await captcha.verify_captcha(None, action="register_teacher"))
        factory.assert_not_called()

    async def test_missing_configuration_fails_closed_before_network(self):
        for setting in (
            "TURNSTILE_SECRET_KEY", "TURNSTILE_SITE_KEY", "TURNSTILE_ALLOWED_HOSTNAMES",
        ):
            previous = getattr(self.settings, setting)
            with self.subTest(setting=setting), patch.object(captcha.httpx, "AsyncClient") as factory:
                setattr(self.settings, setting, " , " if "HOSTNAMES" in setting else " ")
                with self.assertRaises(HTTPException) as failure:
                    await captcha.verify_captcha(TOKEN, action="login")
                self.assert_failure(failure.exception, status=503, code="captcha_unavailable")
                factory.assert_not_called()
            setattr(self.settings, setting, previous)

    async def test_missing_or_oversized_tokens_do_not_reach_provider(self):
        for token, code in (
            (None, "captcha_required"), ("", "captcha_required"), (" \t", "captcha_required"),
            ("x" * 2049, "captcha_invalid"), (42, "captcha_invalid"),
        ):
            with self.subTest(token_type=type(token).__name__, code=code):
                with patch.object(captcha.httpx, "AsyncClient") as factory:
                    with self.assertRaises(HTTPException) as failure:
                        await captcha.verify_captcha(token, action="login")
                    self.assert_failure(failure.exception, status=400, code=code)
                    factory.assert_not_called()

    async def test_valid_tokens_require_expected_action_and_explicit_hostname(self):
        self.settings.TURNSTILE_ALLOWED_HOSTNAMES = " TRACEEDU.APP , www.traceedu.app "
        for action in ("register_teacher", "register_school", "login"):
            with self.subTest(action=action):
                requests = []

                def respond(request):
                    requests.append(request)
                    return httpx.Response(200, json={
                        "success": True, "hostname": "www.traceedu.app", "action": action,
                    })

                client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
                with patch.object(captcha.httpx, "AsyncClient", return_value=client) as factory:
                    with captured_logs() as logs:
                        self.assertIsNone(await captcha.verify_captcha(TOKEN, action=action))
                factory.assert_called_once_with(timeout=3.0)
                request, = requests
                self.assertEqual(request.method, "POST")
                self.assertEqual(str(request.url), captcha._SITEVERIFY_URL)
                self.assertEqual(parse_qs(request.content.decode()), {
                    "secret": [SECRET], "response": [TOKEN],
                })
                self.assertNotIn(TOKEN, logs.getvalue())
                self.assertNotIn(SECRET, logs.getvalue())

    async def test_success_does_not_accept_wrong_host_or_form_action(self):
        for hostname, action in (
            ("evil.example", "login"), ("traceedu.app.evil.example", "login"),
            ("sub.traceedu.app", "login"), (None, "login"),
            ("traceedu.app", "register_teacher"), ("traceedu.app", None),
        ):
            with self.subTest(hostname=hostname, action=action):
                with self.assertRaises(HTTPException) as failure:
                    await self.verify_reply({"success": True, "hostname": hostname, "action": action})
                self.assert_failure(failure.exception, status=400, code="captcha_invalid")

    async def test_expired_duplicate_and_invalid_tokens_are_rejected(self):
        for code in ("timeout-or-duplicate", "invalid-input-response", "missing-input-response"):
            with self.subTest(code=code), self.assertRaises(HTTPException) as failure:
                await self.verify_reply({"success": False, "error-codes": [code]})
            self.assert_failure(failure.exception, status=400, code="captcha_invalid")

    async def test_provider_configuration_and_internal_errors_are_unavailable(self):
        for code in ("missing-input-secret", "invalid-input-secret", "bad-request", "internal-error"):
            with self.subTest(code=code), self.assertRaises(HTTPException) as failure:
                await self.verify_reply({"success": False, "error-codes": [code]})
            self.assert_failure(failure.exception, status=503, code="captcha_unavailable")

    async def test_provider_http_errors_never_fall_back_to_success(self):
        for status in (301, 400, 429, 500, 503):
            with self.subTest(status=status), self.assertRaises(HTTPException) as failure:
                await self.verify_reply({
                    "success": True, "hostname": "traceedu.app", "action": "login",
                }, status=status)
            self.assert_failure(failure.exception, status=503, code="captcha_unavailable")

    async def test_transport_failure_does_not_expose_exception_or_token(self):
        for exception_class in (httpx.ConnectError, httpx.ReadTimeout):
            def respond(request):
                raise exception_class(f"private content: {SECRET} {TOKEN}", request=request)

            client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
            with patch.object(captcha.httpx, "AsyncClient", return_value=client):
                with captured_logs() as logs, self.assertRaises(HTTPException) as failure:
                    await captcha.verify_captcha(TOKEN, action="login")
            self.assert_failure(failure.exception, status=503, code="captcha_unavailable")
            self.assertNotIn(TOKEN, logs.getvalue())
            self.assertNotIn(SECRET, logs.getvalue())

    async def test_malformed_or_truthy_success_responses_fail_closed(self):
        for reply in (
            [], "success", None, {}, {"success": 1}, {"success": "true"},
            {"success": False, "error-codes": "internal-error"},
            {"success": False, "error-codes": [{}]},
        ):
            with self.subTest(reply=reply), self.assertRaises(HTTPException) as failure:
                await self.verify_reply(reply)
            self.assert_failure(failure.exception, status=503, code="captcha_unavailable")

    async def test_non_json_provider_response_is_unavailable_without_body_leak(self):
        def respond(request):
            return httpx.Response(200, text=f"private content: {SECRET} {TOKEN}")

        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        with patch.object(captcha.httpx, "AsyncClient", return_value=client):
            with captured_logs() as logs, self.assertRaises(HTTPException) as failure:
                await captcha.verify_captcha(TOKEN, action="login")
        self.assert_failure(failure.exception, status=503, code="captcha_unavailable")
        self.assertNotIn(TOKEN, logs.getvalue())
        self.assertNotIn(SECRET, logs.getvalue())


if __name__ == "__main__":
    unittest.main()
