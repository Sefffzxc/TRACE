"""Recovery emails use a trusted link origin and safely render supplied names."""
from __future__ import annotations

import os
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pydantic import ValidationError

from app.core.config import Settings, settings
from app.services import email


class EmailIdentityMessageTests(unittest.IsolatedAsyncioTestCase):
    async def test_correction_email_has_fixed_origin_escaped_names_and_five_minute_code(self):
        send = AsyncMock()
        with patch.object(email, "_send_email", send), patch.multiple(
            settings, PUBLIC_FRONTEND_URL="https://www.traceedu.app", OTP_EXPIRY_MINUTES=5,
        ):
            await email.send_email_correction_code(
                "correct@example.com", "001234", challenge_id="opaque-challenge-id",
                full_name="Anne <Teacher>", requested_by="Principal <script>bad</script>",
            )
        args, message = send.await_args
        self.assertEqual(args, ("correct@example.com",))
        self.assertIn("https://www.traceedu.app/correct-email?challenge=opaque-challenge-id", message["text"])
        self.assertIn("001234", message["html"])
        self.assertIn("expires in 5 minutes", message["html"])
        self.assertIn("Anne &lt;Teacher&gt;", message["html"])
        self.assertIn("&lt;script&gt;", message["html"])
        self.assertNotIn("<script>", message["html"])
        self.assertIn('src="cid:trace-logo"', message["html"])

    async def test_previous_inbox_notice_does_not_disclose_replacement_address(self):
        for previous in (True, False):
            with self.subTest(previous=previous):
                send = AsyncMock()
                with patch.object(email, "_send_email", send):
                    await email.send_email_correction_notice(
                        "recipient@example.com", full_name="A <B>",
                        new_email="private-replacement@example.com", is_previous=previous,
                    )
                _, message = send.await_args
                self.assertIn("account and records were preserved", message["text"])
                self.assertIn("A &lt;B&gt;", message["html"])
                if previous:
                    self.assertNotIn("private-replacement@example.com", message["text"])
                    self.assertNotIn("private-replacement@example.com", message["html"])
                else:
                    self.assertIn("private-replacement@example.com", message["text"])

    async def test_both_registration_roles_get_distinct_registration_verification(self):
        for kind in ("teacher", "school"):
            with self.subTest(kind=kind):
                send = AsyncMock()
                with patch.object(email, "_send_email", send), patch.object(settings, "OTP_EXPIRY_MINUTES", 5):
                    await email.send_registration_verification_email("new@example.com", "009912", kind=kind)
                _, message = send.await_args
                self.assertIn("registration", message["subject"])
                self.assertIn("009912", message["text"])
                self.assertIn("expires in 5 minutes", message["text"])
                self.assertIn("application for approval", message["text"])
                self.assertIn('src="cid:trace-logo"', message["html"])


class TrustedFrontendOriginTests(unittest.TestCase):
    def test_origin_rejects_userinfo_paths_and_remote_plain_http(self):
        for value in ("http://untrusted.example", "javascript://host", "https://user:pass@example.com",
                      "https://example.com/path", "https://example.com?query=1", "https://example.com#fragment"):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                Settings(_env_file=None, PUBLIC_FRONTEND_URL=value)

    def test_https_and_local_development_origins_are_allowed(self):
        self.assertEqual(Settings(_env_file=None, PUBLIC_FRONTEND_URL="https://traceedu.app/").PUBLIC_FRONTEND_URL,
                         "https://traceedu.app")
        self.assertEqual(Settings(_env_file=None, PUBLIC_FRONTEND_URL="http://localhost:5173").PUBLIC_FRONTEND_URL,
                         "http://localhost:5173")
