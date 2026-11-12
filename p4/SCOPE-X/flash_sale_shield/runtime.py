"""Asyncio runtime: drives ShieldCore with the wall clock in front of Checkout.

Used by the FastAPI app (``main.py``) and runnable on its own (see
``experiment_engine.experiment_runner --backend inproc``), so the shield's
live behaviour can be checked against the discrete-event testbed.
"""
from __future__ import annotations

import asyncio
import itertools
import time
from collections import deque
from typing import Deque, Dict, Optional

import numpy as np

from .config import CheckoutConfig, ShieldConfig
from .mock_checkout import AsyncMockCheckout
from .queue_manager import ShieldCore


class ShieldRuntime:
    def __init__(self, shield_cfg: Optional[ShieldConfig] = None,
                 checkout_cfg: Optional[CheckoutConfig] = None,
                 seed: Optional[int] = None, window: int = 5000):
        self.t0 = time.monotonic()
        self.core = ShieldCore(shield_cfg or ShieldConfig(), now=0.0)
        self.checkout = AsyncMockCheckout(checkout_cfg or CheckoutConfig(), seed)
        self._ids = itertools.count(1)
        self._waiting: Dict[int, asyncio.Future] = {}
        self._wake = asyncio.Event()
        self._task: Optional[asyncio.Task] = None
        self.latencies_ms: Deque[float] = deque(maxlen=window)
        self.waits_ms: Deque[float] = deque(maxlen=window)
        self.errors = 0

    def now(self) -> float:
        return time.monotonic() - self.t0

    # ------------------------------------------------------------ lifecycle
    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(self._dispatcher())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    # -------------------------------------------------------------- request
    async def submit(self) -> dict:
        rid = next(self._ids)
        now = self.now()
        out = self.core.on_arrival(rid, now)
        if out.status == "rejected":
            return {"status": 503, "result": "rejected", "reason": out.reason,
                    "retry_after_s": out.retry_after_s}
        wait_ms = 0.0
        if out.status == "queued":
            fut = asyncio.get_running_loop().create_future()
            self._waiting[rid] = fut
            self._wake.set()
            try:
                verdict = await fut
            except asyncio.CancelledError:
                # client left; if it was already admitted, give the slot back
                self._waiting.pop(rid, None)
                if fut.done() and not fut.cancelled() and fut.result() == "admit":
                    self.core.on_abandon()
                raise
            if verdict == "expired":
                return {"status": 503, "result": "wait_timeout", "reason": "max_wait_exceeded",
                        "retry_after_s": 1}
            wait_ms = (self.now() - now) * 1000
        self.waits_ms.append(wait_ms)
        try:
            res = await self.checkout.process()
        except asyncio.CancelledError:
            self.core.on_abandon()
            self._wake.set()
            raise
        self.core.on_complete(self.now(), res["latency_ms"])
        self._wake.set()
        self.latencies_ms.append(res["latency_ms"])
        if not res["ok"]:
            self.errors += 1
            return {"status": 504, "result": "checkout_timeout", **res, "wait_ms": wait_ms}
        return {"status": 200, "result": "order_placed", **res, "wait_ms": wait_ms}

    async def _dispatcher(self) -> None:
        while True:
            now = self.now()
            admitted, expired = self.core.dispatch(now)
            for t in admitted:
                fut = self._waiting.pop(t.request_id, None)
                if fut is None or fut.done():
                    self.core.on_abandon()
                else:
                    fut.set_result("admit")
            for t in expired:
                fut = self._waiting.pop(t.request_id, None)
                if fut and not fut.done():
                    fut.set_result("expired")
            wake_at = self.core.next_wakeup(self.now())
            self._wake.clear()
            timeout = None if wake_at is None else max(0.0, wake_at - self.now())
            try:
                await asyncio.wait_for(self._wake.wait(), timeout)
            except asyncio.TimeoutError:
                pass

    # -------------------------------------------------------------- control
    def reconfigure(self, **changes) -> dict:
        cfg = self.core.reconfigure(self.now(), **changes)
        self._wake.set()
        return cfg.to_dict()

    def stats(self) -> dict:
        s = self.core.snapshot(self.now())
        lat = np.asarray(self.latencies_ms) if self.latencies_ms else None
        wait = np.asarray(self.waits_ms) if self.waits_ms else None
        s.update({
            "checkout_errors": self.errors,
            "checkout_p95_ms": float(np.percentile(lat, 95)) if lat is not None else None,
            "checkout_p99_ms": float(np.percentile(lat, 99)) if lat is not None else None,
            "queue_wait_mean_ms": float(wait.mean()) if wait is not None else None,
            "queue_wait_max_ms": float(wait.max()) if wait is not None else None,
            "busy_workers": self.checkout.busy_workers,
            "db_active": self.checkout.db_active,
        })
        return s

    def prometheus(self) -> str:
        """Prometheus text exposition (no client library needed)."""
        s = self.stats()
        lines = []

        def metric(name, kind, value, help_):
            if value is None:
                return
            lines.append(f"# HELP scopex_{name} {help_}")
            lines.append(f"# TYPE scopex_{name} {kind}")
            lines.append(f"scopex_{name} {value}")

        metric("arrivals_total", "counter", s["arrivals"], "Requests reaching the shield")
        metric("admitted_total", "counter", s["admitted"], "Requests admitted to Checkout")
        metric("rejected_total", "counter", s["rejected_full"], "Rejected: waiting room full")
        metric("expired_total", "counter", s["expired"], "Rejected: max wait exceeded")
        metric("completed_total", "counter", s["completed"], "Checkout completions")
        metric("checkout_errors_total", "counter", s["checkout_errors"], "Checkout timeouts")
        metric("queue_length", "gauge", s["queue_length"], "Requests in waiting room")
        metric("inflight", "gauge", s["inflight"], "Requests inside Checkout")
        metric("tokens", "gauge", s["tokens"], "Tokens in bucket")
        metric("checkout_latency_p95_ms", "gauge", s["checkout_p95_ms"], "Recent p95 Checkout latency")
        metric("checkout_latency_p99_ms", "gauge", s["checkout_p99_ms"], "Recent p99 Checkout latency")
        metric("queue_wait_mean_ms", "gauge", s["queue_wait_mean_ms"], "Recent mean waiting time")
        return "\n".join(lines) + "\n"
