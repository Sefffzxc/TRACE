"""Short-lived failure counts for login CAPTCHA, including unknown accounts.

Account failures also use the existing database lockout counters. This bounded
IP history is per API worker; TRACE's deployment uses one worker. Successful
logins do not clear a shared IP's failure history. No email addresses are stored.
"""
from collections import OrderedDict, deque
import time


class LoginRisk:
    def __init__(self, window_seconds=900, max_ips=10000):
        self.window_seconds = window_seconds
        self.max_ips = max_ips
        self.failures = OrderedDict()

    def _prune(self, now):
        cutoff = now - self.window_seconds
        while self.failures:
            first = next(iter(self.failures))
            if self.failures[first][-1] > cutoff:
                break
            self.failures.popitem(last=False)

    def requires_captcha(self, ip, threshold):
        now = time.monotonic()
        self._prune(now)
        history = self.failures.get(ip)
        if history is None:
            return False
        while history and history[0] <= now - self.window_seconds:
            history.popleft()
        return len(history) >= threshold

    def record_failure(self, ip):
        now = time.monotonic()
        self._prune(now)
        history = self.failures.setdefault(ip, deque(maxlen=20))
        history.append(now)
        self.failures.move_to_end(ip)
        while len(self.failures) > self.max_ips:
            self.failures.popitem(last=False)

    def clear(self):
        self.failures.clear()


login_risk = LoginRisk()
