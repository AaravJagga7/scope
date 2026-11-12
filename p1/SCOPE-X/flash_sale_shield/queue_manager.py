"""ShieldCore: the Flash-sale Shield decision logic, independent of any clock.

Request path::

    arrival -> [backpressure: queue full?] -> reject 503
            -> [queue empty AND admission ok AND token] -> admit to Checkout
            -> otherwise wait in the FIFO waiting room
    dispatcher: while head-of-queue exists AND admission ok AND token -> admit

FIFO fairness: a new arrival is never admitted ahead of someone already
waiting - it can only bypass the queue when the queue is empty.

Both runtimes drive this same object:
* ``flash_sale_shield.main`` (FastAPI, wall clock), and
* ``experiment_engine.simulator`` (discrete-event testbed, virtual clock).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

from .admission_controller import AdmissionController
from .backpressure import Backpressure, Pressure
from .config import ShieldConfig
from .rate_limiter import TokenBucket
from .waiting_room import Ticket, WaitingRoom


@dataclass
class ArrivalOutcome:
    status: str                    # "admitted" | "queued" | "rejected"
    ticket: Optional[Ticket] = None
    reason: str = ""
    retry_after_s: int = 0
    pressure: Pressure = Pressure.NORMAL


class ShieldCore:
    def __init__(self, config: Optional[ShieldConfig] = None, now: float = 0.0):
        self.config = config or ShieldConfig()
        self.config.validate()
        c = self.config
        self.bucket = TokenBucket(c.admission_rate, c.bucket_capacity, now)
        self.room = WaitingRoom(c.queue_capacity, c.max_wait_s)
        self.backpressure = Backpressure(c.queue_soft_limit, c.downstream_limit)
        self.admission = AdmissionController(c.max_inflight, self.backpressure,
                                             c.latency_guard_ms, c.latency_ewma_alpha)
        self.inflight = 0
        self.arrivals = 0
        self.admitted = 0
        self.admitted_direct = 0
        self.rejected = 0
        self.completed = 0

    # ------------------------------------------------------------------ API
    def on_arrival(self, request_id: int, now: float, payload: Any = None) -> ArrivalOutcome:
        self.arrivals += 1
        ticket = Ticket(request_id, arrival=now, enqueued=now, payload=payload)
        pressure = self.backpressure.queue_state(len(self.room), self.room.capacity)

        if len(self.room) == 0 and self._can_admit(now):
            self._admit(direct=True)
            return ArrivalOutcome("admitted", ticket, pressure=pressure)

        if self.room.offer(ticket):
            return ArrivalOutcome("queued", ticket, pressure=pressure)

        self.rejected += 1
        return ArrivalOutcome(
            "rejected", ticket, reason="waiting_room_full",
            retry_after_s=self.backpressure.retry_after_s(len(self.room), self.bucket.rate),
            pressure=Pressure.SATURATED)

    def dispatch(self, now: float) -> Tuple[List[Ticket], List[Ticket]]:
        """Release as many waiting requests as admission and tokens allow.

        Returns ``(admitted, expired)``; the caller forwards the admitted
        tickets to Checkout and answers the expired ones with 503.
        """
        expired = self.room.expire(now)
        admitted: List[Ticket] = []
        while len(self.room) and self._can_admit(now):
            admitted.append(self.room.poll())
            self._admit(direct=False)
        return admitted, expired

    def on_complete(self, now: float, checkout_latency_ms: float) -> None:
        self.inflight -= 1
        self.completed += 1
        self.admission.observe_latency(checkout_latency_ms)

    def on_abandon(self) -> None:
        """An admitted request was dropped before Checkout (client went away)."""
        self.inflight -= 1

    def next_wakeup(self, now: float) -> Optional[float]:
        """Earliest time ``dispatch`` could make progress without a completion.

        ``None`` means "only a completion (or new arrival) can unblock it".
        """
        if not len(self.room):
            return None
        times = []
        exp = self.room.next_expiry()
        if exp is not None:
            times.append(exp)
        if self._admission_ok_dry():
            times.append(now + self.bucket.time_until_available(now))
        return min(times) if times else None

    def reconfigure(self, now: float, **changes: Any) -> ShieldConfig:
        self.config.update(**changes)
        c = self.config
        self.bucket.reconfigure(now, c.admission_rate, c.bucket_capacity)
        self.room.resize(c.queue_capacity)
        self.room.max_wait_s = c.max_wait_s
        self.backpressure.queue_soft_limit = c.queue_soft_limit
        self.backpressure.downstream_limit = c.downstream_limit
        self.admission.max_inflight = c.max_inflight
        self.admission.latency_guard_ms = c.latency_guard_ms
        self.admission.ewma_alpha = c.latency_ewma_alpha
        return self.config

    def snapshot(self, now: float) -> dict:
        return {
            "arrivals": self.arrivals,
            "admitted": self.admitted,
            "admitted_direct": self.admitted_direct,
            "rejected_full": self.room.rejected_full,
            "expired": self.room.expired,
            "completed": self.completed,
            "inflight": self.inflight,
            "queue_length": len(self.room),
            "queue_capacity": self.room.capacity,
            "queue_max_seen": self.room.max_length_seen,
            "tokens": round(self.bucket.available(now), 3),
            "pressure": self.backpressure.queue_state(len(self.room), self.room.capacity).value,
            "admission_blocked": dict(self.admission.blocked),
            "latency_ewma_ms": self.admission.latency_ewma_ms,
            "config": self.config.to_dict(),
        }

    # ------------------------------------------------------------ internals
    def _admission_ok_dry(self) -> bool:
        """Admission check without touching the blocked-counters."""
        a = self.admission
        if self.inflight >= a.max_inflight:
            return False
        if self.inflight >= self.backpressure.downstream_limit:
            return False
        if (a.latency_guard_ms > 0 and a.latency_ewma_ms is not None
                and a.latency_ewma_ms > a.latency_guard_ms and self.inflight > 0):
            return False
        return True

    def _can_admit(self, now: float) -> bool:
        # Check system conditions first so a token is not burned on a
        # request that admission control would block anyway.
        if not self._admission_ok_dry():
            self.admission.decide(self.inflight)   # records why it blocked
            return False
        return self.bucket.try_acquire(now)

    def _admit(self, direct: bool) -> None:
        self.inflight += 1
        self.admitted += 1
        if direct:
            self.admitted_direct += 1
