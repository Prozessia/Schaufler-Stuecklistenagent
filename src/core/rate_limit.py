"""SEC-004 — In-memory rate limiter and login lockout.

Intentionally simple: single-instance deployment, no Redis dependency.
Thread-safe via a shared Lock. Old entries are cleaned up inline.
"""

from __future__ import annotations

import time
from collections import deque
from threading import Lock
from typing import Deque


class RateLimiter:
    """Sliding-window rate limiter.

    Tracks *all* attempts (not just failures). Call ``allow(key)`` before
    processing a request; it returns ``False`` when the limit is exceeded.

    Note: Single-instance deployment only — state is in-process memory.
    """

    def __init__(self, max_attempts: int, window_seconds: float) -> None:
        self._max = max_attempts
        self._window = window_seconds
        self._buckets: dict[str, Deque[float]] = {}
        self._lock = Lock()

    def allow(self, key: str) -> bool:
        """Record an attempt and return ``True`` if within the limit."""
        now = time.monotonic()
        cutoff = now - self._window
        with self._lock:
            dq = self._buckets.setdefault(key, deque())
            # Evict old entries
            while dq and dq[0] <= cutoff:
                dq.popleft()
            if len(dq) >= self._max:
                return False
            dq.append(now)
            return True

    def _cleanup(self) -> None:
        """Remove empty buckets (optional maintenance, not required)."""
        now = time.monotonic()
        cutoff = now - self._window
        with self._lock:
            stale = [k for k, dq in self._buckets.items() if not dq or dq[-1] <= cutoff]
            for k in stale:
                self._buckets.pop(k, None)


class LoginLockout:
    """Account lockout after repeated failed login attempts.

    Tracks failures per key (typically ``(ip, username)``).  Once
    ``max_failures`` failures accumulate within the current lockout window the
    key is locked for ``lockout_seconds``.  A successful login resets the
    counter via ``reset(key)``.

    Note: Single-instance deployment only — state is in-process memory.
    """

    def __init__(self, max_failures: int = 8, lockout_seconds: float = 900) -> None:
        self._max = max_failures
        self._lockout = lockout_seconds
        # key -> (failure_count, locked_until_monotonic | None)
        self._state: dict[str, tuple[int, float | None]] = {}
        self._lock = Lock()

    def is_locked(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            count, locked_until = self._state.get(key, (0, None))
            if locked_until is not None and now < locked_until:
                return True
            # Expired lock — clean up
            if locked_until is not None and now >= locked_until:
                self._state.pop(key, None)
            return False

    def register_failure(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            count, locked_until = self._state.get(key, (0, None))
            # If there was an expired lock, reset
            if locked_until is not None and now >= locked_until:
                count = 0
                locked_until = None
            count += 1
            new_locked: float | None = (now + self._lockout) if count >= self._max else None
            self._state[key] = (count, new_locked)

    def reset(self, key: str) -> None:
        with self._lock:
            self._state.pop(key, None)
