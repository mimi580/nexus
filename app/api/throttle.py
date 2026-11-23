"""Brute-force protection for the operator login.

Failed logins are counted per client address. After MAX_FAILURES within
WINDOW_SECONDS the address is refused (HTTP 429, even with the right password)
until the window clears. A successful login resets the count. State is in
memory: it resets when the API restarts, which is acceptable for a
single-process deployment behind Caddy.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

MAX_FAILURES = 8
WINDOW_SECONDS = 15 * 60
MAX_TRACKED_CLIENTS = 5000


class LoginThrottle:
    def __init__(self, max_failures: int = MAX_FAILURES, window_seconds: int = WINDOW_SECONDS) -> None:
        self.max_failures = max_failures
        self.window = window_seconds
        self._failures: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> deque[float]:
        events = self._failures[key]
        while events and now - events[0] > self.window:
            events.popleft()
        if not events:
            self._failures.pop(key, None)
        return events

    def retry_after(self, key: str, now: float | None = None) -> int:
        """Seconds the client must wait, or 0 if it may try."""
        now = time.monotonic() if now is None else now
        with self._lock:
            events = self._prune(key, now)
            if len(events) < self.max_failures:
                return 0
            return max(1, int(self.window - (now - events[0])) + 1)

    def record_failure(self, key: str, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        with self._lock:
            if len(self._failures) >= MAX_TRACKED_CLIENTS and key not in self._failures:
                # Bound memory under a distributed attack: forget the oldest client.
                oldest = min(self._failures, key=lambda k: self._failures[k][-1])
                self._failures.pop(oldest, None)
            self._failures[key].append(now)

    def record_success(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)

    def reset(self) -> None:
        with self._lock:
            self._failures.clear()


login_throttle = LoginThrottle()
