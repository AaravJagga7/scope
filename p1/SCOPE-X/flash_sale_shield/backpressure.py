"""Backpressure: tells upstream components when downstream cannot keep up.

Two signals are combined:
* waiting-room saturation (fill ratio of the bounded queue), and
* Checkout saturation (requests currently in flight inside Checkout).

The state is exposed to the admission controller (stop releasing work) and
to clients (HTTP 503 + Retry-After when the waiting room is full).
"""
from __future__ import annotations

from enum import Enum


class Pressure(str, Enum):
    NORMAL = "normal"        # accept and admit
    WARN = "warn"            # accept, but queue is filling: hint clients
    SATURATED = "saturated"  # queue full: reject new arrivals


class Backpressure:
    def __init__(self, queue_soft_limit: float = 0.8, downstream_limit: int = 150):
        self.queue_soft_limit = queue_soft_limit
        self.downstream_limit = downstream_limit
        self.downstream_pauses = 0

    def queue_state(self, queue_len: int, queue_capacity: int) -> Pressure:
        if queue_len >= queue_capacity:
            return Pressure.SATURATED
        if queue_capacity and queue_len / queue_capacity >= self.queue_soft_limit:
            return Pressure.WARN
        return Pressure.NORMAL

    def downstream_ok(self, inflight: int) -> bool:
        ok = inflight < self.downstream_limit
        if not ok:
            self.downstream_pauses += 1
        return ok

    def retry_after_s(self, queue_len: int, admission_rate: float) -> int:
        """A rough client hint: time for the current queue to drain."""
        return max(1, int(round(queue_len / max(admission_rate, 1e-9))))
