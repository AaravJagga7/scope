"""Admission control: may a request leave the waiting room for Checkout now?

Unlike the rate limiter (which only enforces a rate), the admission
controller looks at system conditions:
* in-flight requests inside Checkout (a concurrency cap),
* the downstream backpressure signal, and
* optionally, recent Checkout latency (an EWMA latency guard).
"""
from __future__ import annotations

from dataclasses import dataclass

from .backpressure import Backpressure


@dataclass
class AdmissionDecision:
    admit: bool
    reason: str = "ok"


class AdmissionController:
    def __init__(self, max_inflight: int, backpressure: Backpressure,
                 latency_guard_ms: float = 0.0, ewma_alpha: float = 0.2):
        self.max_inflight = max_inflight
        self.backpressure = backpressure
        self.latency_guard_ms = latency_guard_ms
        self.ewma_alpha = ewma_alpha
        self.latency_ewma_ms: float | None = None
        self.blocked = {"inflight": 0, "downstream": 0, "latency": 0}

    def observe_latency(self, latency_ms: float) -> None:
        if self.latency_ewma_ms is None:
            self.latency_ewma_ms = latency_ms
        else:
            a = self.ewma_alpha
            self.latency_ewma_ms = a * latency_ms + (1 - a) * self.latency_ewma_ms

    def decide(self, inflight: int) -> AdmissionDecision:
        if inflight >= self.max_inflight:
            self.blocked["inflight"] += 1
            return AdmissionDecision(False, "inflight_cap")
        if not self.backpressure.downstream_ok(inflight):
            self.blocked["downstream"] += 1
            return AdmissionDecision(False, "downstream_backpressure")
        if (self.latency_guard_ms > 0 and self.latency_ewma_ms is not None
                and self.latency_ewma_ms > self.latency_guard_ms
                and inflight > 0):
            # inflight > 0 guarantees a completion will arrive and update the
            # EWMA, so the guard can never deadlock an idle system.
            self.blocked["latency"] += 1
            return AdmissionDecision(False, "latency_guard")
        return AdmissionDecision(True)
