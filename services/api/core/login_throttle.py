"""Brute-force protection of POST /login (OWASP A07 Identification and Authentication Failures).

After LOGIN_MAX_FAILURES failed attempts for the same username within LOGIN_WINDOW_SECONDS, that
username is refused with 429 + Retry-After — without even checking the password — until the oldest
failure leaves the window. A successful login clears its failures.

Keyed by username, not by IP: every browser login reaches the API through the Next server (one IP),
so an IP counter would lock out everybody at once. The temporary per-username lockout is the usual
trade-off (an attacker can delay one account for the window, never take it over). One instance per
API process (app.state), in memory: with several workers each keeps its own count — the host-level
fail2ban and the reverse proxy are the outer layers.
"""

from __future__ import annotations

import hashlib
import os
import threading
import time
from collections import deque


def _setting(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, default)))
    except ValueError:
        return default


def fingerprint(username: str) -> str:
    """What the logs carry instead of the username."""
    return hashlib.sha256(username.lower().encode()).hexdigest()[:12]


class LoginThrottle:
    def __init__(self, max_failures: int | None = None, window_seconds: int | None = None, clock=time.monotonic):
        self.max_failures = max_failures or _setting("LOGIN_MAX_FAILURES", 5)
        self.window = window_seconds or _setting("LOGIN_WINDOW_SECONDS", 15 * 60)
        self.clock = clock
        self._failures: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> deque[float]:
        failures = self._failures.setdefault(key, deque())
        while failures and now - failures[0] >= self.window:
            failures.popleft()
        return failures

    def retry_after(self, username: str) -> int | None:
        key, now = username.lower(), self.clock()
        with self._lock:
            failures = self._prune(key, now)
            if len(failures) < self.max_failures:
                return None
            return max(1, int(failures[0] + self.window - now) + 1)

    def failed(self, username: str) -> int:
        key, now = username.lower(), self.clock()
        with self._lock:
            failures = self._prune(key, now)
            failures.append(now)
            return len(failures)

    def succeeded(self, username: str) -> None:
        with self._lock:
            self._failures.pop(username.lower(), None)
