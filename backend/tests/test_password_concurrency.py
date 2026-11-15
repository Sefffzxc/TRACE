"""Password operations must leave the API event loop available to requests.

Run from backend: python -m unittest discover -s tests -p test_password_concurrency.py
The tests use synthetic passwords and never open the application's database.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core import security


class PasswordConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def _assert_nonblocking(self, helper, sync_name, arguments, result):
        """A simulated slow password check may not stall another request."""
        started = threading.Event()
        release = threading.Event()
        event_loop_thread = threading.get_ident()
        worker_threads = []

        def slow_password_operation(*received):
            self.assertEqual(received, arguments)
            worker_threads.append(threading.get_ident())
            started.set()
            # The timeout prevents a broken implementation from hanging tests.
            release.wait(timeout=2)
            return result

        with patch.object(security, sync_name, side_effect=slow_password_operation):
            operation = asyncio.create_task(helper(*arguments))
            try:
                self.assertTrue(await asyncio.to_thread(started.wait, 1))
                # This represents a health/admin request scheduled while bcrypt
                # is still working. It must run before password work finishes.
                await asyncio.sleep(0)
                self.assertFalse(operation.done(), "Password work blocked the event loop")
                self.assertNotEqual(worker_threads, [event_loop_thread])
            finally:
                release.set()
                actual = await asyncio.wait_for(operation, timeout=3)
        self.assertEqual(actual, result)

    async def test_hashing_leaves_the_event_loop_available(self):
        await self._assert_nonblocking(
            security.async_get_password_hash, "get_password_hash",
            ("SyntheticPassword1!",), "synthetic-hash",
        )

    async def test_password_verification_leaves_the_event_loop_available(self):
        for valid in (True, False):
            with self.subTest(valid=valid):
                await self._assert_nonblocking(
                    security.async_verify_password, "verify_password",
                    ("SyntheticPassword1!", "synthetic-hash"), valid,
                )

    async def test_real_bcrypt_cost_and_verification_are_preserved(self):
        password = "SyntheticPassword1!"
        hashed = await security.async_get_password_hash(password)
        self.assertEqual(hashed.split("$")[2], "12")
        self.assertTrue(await security.async_verify_password(password, hashed))
        self.assertFalse(await security.async_verify_password("WrongPassword1!", hashed))
        self.assertFalse(await security.async_verify_password(password, "invalid-hash"))


if __name__ == "__main__":
    unittest.main()
