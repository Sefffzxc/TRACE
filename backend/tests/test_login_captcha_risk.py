"""Deterministic expiry and memory limits for the temporary login IP counter.

No database, network, wall-clock waits, or real account data are used.
"""
from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import Settings
from app.services import login_captcha
from app.services.login_captcha import LoginRisk


class LoginCaptchaRiskTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000.0
        clock = patch.object(login_captcha, "time", SimpleNamespace(monotonic=lambda: self.now))
        clock.start()
        self.addCleanup(clock.stop)

    def record(self, risk, ip, count=3):
        for _ in range(count):
            risk.record_failure(ip)

    def test_three_failures_expire_exactly_at_fifteen_minutes(self):
        risk = LoginRisk()
        self.record(risk, "198.51.100.1")
        self.assertTrue(risk.requires_captcha("198.51.100.1", 3))
        self.now = 1899.999
        self.assertTrue(risk.requires_captcha("198.51.100.1", 3))
        self.now = 1900.0
        self.assertFalse(risk.requires_captcha("198.51.100.1", 3))

    def test_threshold_uses_a_rolling_window_without_renewing_older_failures(self):
        risk = LoginRisk()
        risk.record_failure("198.51.100.1")
        self.now = 1400.0
        risk.record_failure("198.51.100.1")
        self.now = 1899.0
        risk.record_failure("198.51.100.1")
        self.assertTrue(risk.requires_captcha("198.51.100.1", 3))
        self.now = 1900.0
        self.assertFalse(risk.requires_captcha("198.51.100.1", 3))
        risk.record_failure("198.51.100.1")
        self.assertTrue(risk.requires_captcha("198.51.100.1", 3))
        self.now = 2300.0
        self.assertFalse(risk.requires_captcha("198.51.100.1", 3))

    def test_bounded_counter_evicts_the_ip_with_oldest_failure_activity(self):
        risk = LoginRisk(max_ips=2)
        self.record(risk, "198.51.100.1")
        self.now += 1
        self.record(risk, "198.51.100.2")
        self.assertTrue(risk.requires_captcha("198.51.100.1", 3))
        self.now += 1
        self.record(risk, "198.51.100.3")
        self.assertFalse(risk.requires_captcha("198.51.100.1", 3))
        self.assertTrue(risk.requires_captcha("198.51.100.2", 3))
        self.assertTrue(risk.requires_captcha("198.51.100.3", 3))
        self.assertLessEqual(len(risk.failures), 2)
        self.now += 1
        risk.record_failure("198.51.100.2")
        self.now += 1
        self.record(risk, "198.51.100.4")
        self.assertFalse(risk.requires_captcha("198.51.100.3", 3))
        self.assertTrue(risk.requires_captcha("198.51.100.2", 3))
        self.assertTrue(risk.requires_captcha("198.51.100.4", 3))
        self.assertLessEqual(len(risk.failures), 2)

    def test_supported_thresholds_are_reachable_without_unbounded_history(self):
        for threshold in (1, 3, 20):
            with self.subTest(threshold=threshold):
                risk = LoginRisk()
                self.assertFalse(risk.requires_captcha("198.51.100.1", threshold))
                self.record(risk, "198.51.100.1", threshold - 1)
                self.assertFalse(risk.requires_captcha("198.51.100.1", threshold))
                risk.record_failure("198.51.100.1")
                self.assertTrue(risk.requires_captcha("198.51.100.1", threshold))
                self.record(risk, "198.51.100.1", 100)
                self.assertTrue(risk.requires_captcha("198.51.100.1", threshold))
                self.assertLessEqual(len(risk.failures["198.51.100.1"]), 20)

    def test_configuration_cannot_set_threshold_above_retained_history(self):
        for threshold in (1, 3, 20):
            with self.subTest(threshold=threshold):
                configured = Settings(_env_file=None, CAPTCHA_LOGIN_THRESHOLD=threshold)
                self.assertEqual(configured.CAPTCHA_LOGIN_THRESHOLD, threshold)
        for threshold in (0, 21):
            with self.subTest(threshold=threshold), self.assertRaises(ValidationError):
                Settings(_env_file=None, CAPTCHA_LOGIN_THRESHOLD=threshold)

    def test_counter_retains_only_ip_keys_and_numeric_failure_times(self):
        risk = LoginRisk()
        ips = ("198.51.100.1", "2001:db8::1")
        for ip in ips:
            self.record(risk, ip)
        self.assertEqual(set(risk.failures), set(ips))
        for times in risk.failures.values():
            self.assertTrue(all(isinstance(value, (int, float)) for value in times))
        risk.clear()
        self.assertFalse(risk.requires_captcha(ips[0], 3))
        self.assertFalse(risk.requires_captcha(ips[1], 3))


if __name__ == "__main__":
    unittest.main()
