"""Token-bucket rate limiter.

Tokens are added at ``rate`` per second up to ``capacity``. Each admitted
request consumes one token. The limiter takes the current time as an
argument instead of reading a clock, so the same code runs in the live
FastAPI service (wall clock) and in the discrete-event testbed (virtual
clock).
"""
from __future__ import annotations

import math


class TokenBucket:
    def __init__(self, rate: float, capacity: int, now: float = 0.0):
        if rate <= 0 or capacity < 1:
            raise ValueError("rate must be > 0 and capacity >= 1")
        self.rate = float(rate)
        self.capacity = int(capacity)
        self.tokens = float(capacity)        # start full
        self.last = now
        self.granted = 0
        self.denied = 0

    def _refill(self, now: float) -> None:
        if now > self.last:
            self.tokens = min(self.capacity, self.tokens + (now - self.last) * self.rate)
            self.last = now

    def try_acquire(self, now: float, n: int = 1) -> bool:
        self._refill(now)
        if self.tokens >= n - 1e-9:
            self.tokens -= n
            self.granted += n
            return True
        self.denied += n
        return False

    def available(self, now: float) -> float:
        self._refill(now)
        return self.tokens

    def time_until_available(self, now: float, n: int = 1) -> float:
        """Seconds until ``n`` tokens will be available (0 if already)."""
        self._refill(now)
        missing = n - self.tokens
        return 0.0 if missing <= 1e-9 else missing / self.rate

    def reconfigure(self, now: float, rate: float | None = None,
                    capacity: int | None = None) -> None:
        self._refill(now)
        if rate is not None:
            if rate <= 0:
                raise ValueError("rate must be > 0")
            self.rate = float(rate)
        if capacity is not None:
            if capacity < 1:
                raise ValueError("capacity must be >= 1")
            self.capacity = int(capacity)
            self.tokens = min(self.tokens, self.capacity)

    def __repr__(self) -> str:  # pragma: no cover
        return f"TokenBucket(rate={self.rate}, capacity={self.capacity}, tokens={self.tokens:.2f})"
