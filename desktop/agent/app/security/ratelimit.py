"""In-memory token-bucket rate limiting, keyed by device or source address."""

from __future__ import annotations

import time
from collections.abc import Callable


class RateLimiter:
    """Allows `capacity` events per `per_seconds` for each key, refilling continuously."""

    def __init__(
        self, capacity: int, per_seconds: float, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.capacity = float(capacity)
        self.refill_per_s = capacity / per_seconds
        self._clock = clock
        self._buckets: dict[str, tuple[float, float]] = {}

    def allow(self, key: str) -> bool:
        now = self._clock()
        tokens, last = self._buckets.get(key, (self.capacity, now))
        tokens = min(self.capacity, tokens + (now - last) * self.refill_per_s)
        if tokens < 1.0:
            self._buckets[key] = (tokens, now)
            return False
        self._buckets[key] = (tokens - 1.0, now)
        return True

    def retry_after(self, key: str) -> int:
        tokens, _ = self._buckets.get(key, (self.capacity, 0.0))
        return max(1, int((1.0 - tokens) / self.refill_per_s) + 1)


class Lockout:
    """Blocks a key for `lock_seconds` after `max_failures` consecutive failures."""

    def __init__(
        self, max_failures: int, lock_seconds: float, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.max_failures = max_failures
        self.lock_seconds = lock_seconds
        self._clock = clock
        self._failures: dict[str, int] = {}
        self._locked_until: dict[str, float] = {}

    def is_locked(self, key: str) -> bool:
        until = self._locked_until.get(key)
        if until is None:
            return False
        if self._clock() >= until:
            del self._locked_until[key]
            self._failures.pop(key, None)
            return False
        return True

    def failure(self, key: str) -> None:
        count = self._failures.get(key, 0) + 1
        self._failures[key] = count
        if count >= self.max_failures:
            self._locked_until[key] = self._clock() + self.lock_seconds

    def success(self, key: str) -> None:
        self._failures.pop(key, None)
