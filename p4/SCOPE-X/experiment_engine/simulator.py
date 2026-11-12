"""Discrete-event testbed surrogate: Flash-sale Shield + mock Checkout + shared DB.

This is the stand-in "testbed" for Milestones 1-3, before the team's
Kubernetes environment exists. It drives the *real* shield logic
(``flash_sale_shield.ShieldCore``) on a virtual clock, so a 16-second
flash-sale experiment runs in well under a second and is reproducible
from a seed. The service model is documented in
``flash_sale_shield/mock_checkout.py``.

Swap this for the Kubernetes backend in Milestone 5; the experiment runner
and OR engine only see the returned measurement row.
"""
from __future__ import annotations

import heapq
import math
import random
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np

from flash_sale_shield.config import CheckoutConfig, ShieldConfig
from flash_sale_shield.mock_checkout import db_write_mean_ms, lognormal_ms
from flash_sale_shield.queue_manager import ShieldCore

from .experiment_config import DEFAULT_PROFILE, DEFAULT_SAFETY, SafetyLimits, WorkloadProfile
from .workload_generator import arrival_times, offered_rate

ARR, WAKE, APP_DONE, DB_DONE, SAMPLE = range(5)
PENDING, QUEUED, REJECTED, EXPIRED, ADMITTED = range(5)


@dataclass
class SimResult:
    metrics: Dict[str, float]
    timeseries: Dict[str, np.ndarray] = field(default_factory=dict)


class Simulator:
    def __init__(self, shield_cfg: ShieldConfig, checkout_cfg: CheckoutConfig,
                 profile: WorkloadProfile = DEFAULT_PROFILE,
                 safety: SafetyLimits = DEFAULT_SAFETY, seed: int = 0,
                 warmup_s: float = 1.0, drain_s: float = 6.0):
        self.scfg, self.ccfg = shield_cfg, checkout_cfg
        self.profile, self.safety = profile, safety
        self.seed, self.warmup_s, self.drain_s = seed, warmup_s, drain_s

    # =================================================================== run
    def run(self, peak_rate: float) -> SimResult:
        rng_np = np.random.default_rng(self.seed)
        self.rng = random.Random(self.seed + 7919)
        arrivals = arrival_times(peak_rate, self.profile, rng_np)
        n = len(arrivals)
        self.t_arr = arrivals
        self.t_admit = np.full(n, np.nan)
        self.t_done = np.full(n, np.nan)
        self.db_ms = np.full(n, np.nan)
        self.status = np.zeros(n, dtype=np.int8)

        self.core = ShieldCore(ShieldConfig(**self.scfg.to_dict()), now=0.0)
        c = self.ccfg
        self.n_workers = c.replicas * c.workers_per_replica
        self.workers_free = self.n_workers
        self.worker_q: deque = deque()
        self.db_free = c.db_pool_size
        self.db_q: deque = deque()
        self.db_active = 0
        self.busy_area = 0.0       # integral of occupied workers dt
        self.cpu_area = 0.0        # integral of workers in the app (CPU) phase dt
        self.db_area = 0.0
        self.app_busy = 0
        self.last_t = 0.0
        self.busy = 0

        self.heap: list = []
        self.seq = 0
        self.next_wake: Optional[float] = None
        for i, t in enumerate(arrivals):
            self._push(t, ARR, i)
        t_end = self.profile.pre_s + self.profile.burst_s + self.profile.post_s
        self.t_end = t_end
        self._push(self.safety.window_s, SAMPLE, None)

        ts = {k: [] for k in ("t", "offered", "arrived", "admitted", "completed",
                              "queue_len", "inflight", "p95_window", "cpu")}
        win_lat: List[float] = []
        win_counts = {"arrived": 0, "admitted": 0, "completed": 0}
        self._win_counts = win_counts
        self._win_lat = win_lat
        last_busy_area = 0.0
        strikes_lat = strikes_cpu = 0
        aborted, abort_reason = False, ""
        now = 0.0

        while self.heap:
            t, _, kind, data = heapq.heappop(self.heap)
            if t > t_end + self.drain_s:
                break
            self._advance(t)
            now = t
            if kind == ARR:
                self._on_arrival(data, now)
            elif kind == WAKE:
                if self.next_wake is not None and abs(self.next_wake - t) < 1e-12:
                    self.next_wake = None
                self._dispatch(now)
            elif kind == APP_DONE:
                self._on_app_done(data, now)
            elif kind == DB_DONE:
                self._on_db_done(data, now)
            elif kind == SAMPLE:
                w = self.safety.window_s
                cpu = (self.cpu_area - last_busy_area) / (self.n_workers * w)
                last_busy_area = self.cpu_area
                p95w = float(np.percentile(win_lat, 95)) if win_lat else 0.0
                # in-flight requests older than the stop limit count as slow too
                ts["t"].append(now)
                ts["offered"].append(offered_rate(now - 1e-9, peak_rate, self.profile))
                ts["arrived"].append(win_counts["arrived"] / w)
                ts["admitted"].append(win_counts["admitted"] / w)
                ts["completed"].append(win_counts["completed"] / w)
                ts["queue_len"].append(len(self.core.room))
                ts["inflight"].append(self.core.inflight)
                ts["p95_window"].append(p95w)
                ts["cpu"].append(cpu)
                for k in win_counts:
                    win_counts[k] = 0
                win_lat.clear()

                strikes_lat = strikes_lat + 1 if p95w > self.safety.L_stop_ms else 0
                strikes_cpu = strikes_cpu + 1 if cpu >= self.safety.U_stop else 0
                if strikes_lat >= self.safety.patience_latency:
                    aborted, abort_reason = True, "latency_stop"
                elif strikes_cpu >= self.safety.patience_cpu:
                    aborted, abort_reason = True, "cpu_stop"
                if aborted:
                    break
                idle = (now >= t_end and self.core.inflight == 0 and len(self.core.room) == 0)
                if not idle:
                    self._push(now + w, SAMPLE, None)

        metrics = self._finalize(now, peak_rate, aborted, abort_reason)
        return SimResult(metrics, {k: np.asarray(v) for k, v in ts.items()})

    # ============================================================= internals
    def _push(self, t: float, kind: int, data) -> None:
        self.seq += 1
        heapq.heappush(self.heap, (t, self.seq, kind, data))

    def _advance(self, t: float) -> None:
        dt = t - self.last_t
        if dt > 0:
            self.busy_area += self.busy * dt
            self.cpu_area += self.app_busy * dt
            self.db_area += self.db_active * dt
            self.last_t = t

    def _schedule_wake(self, now: float) -> None:
        wake = self.core.next_wakeup(now)
        if wake is None:
            return
        wake = max(wake + 1e-9, now + 1e-9)
        if self.next_wake is not None and now <= self.next_wake <= wake:
            return                       # an earlier wake will recompute
        self.next_wake = wake
        self._push(wake, WAKE, None)

    def _on_arrival(self, i: int, now: float) -> None:
        self._win_counts["arrived"] += 1
        out = self.core.on_arrival(i, now)
        if out.status == "admitted":
            self._admit(i, now)
        elif out.status == "queued":
            self.status[i] = QUEUED
        else:
            self.status[i] = REJECTED
        self._dispatch(now)

    def _dispatch(self, now: float) -> None:
        admitted, expired = self.core.dispatch(now)
        for t in admitted:
            self._admit(t.request_id, now)
        for t in expired:
            self.status[t.request_id] = EXPIRED
        self._schedule_wake(now)

    def _admit(self, i: int, now: float) -> None:
        self.status[i] = ADMITTED
        self.t_admit[i] = now
        self._win_counts["admitted"] += 1
        if self.workers_free > 0:
            self.workers_free -= 1
            self.busy += 1
            self._start_app(i, now)
        else:
            self.worker_q.append(i)

    def _start_app(self, i: int, now: float) -> None:
        self.app_busy += 1
        self._push(now + lognormal_ms(self.rng, self.ccfg.app_time_ms, self.ccfg.service_cv) / 1000,
                   APP_DONE, i)

    def _start_db(self, i: int, now: float) -> None:
        self.db_active += 1
        mean = db_write_mean_ms(self.ccfg, self.db_active)
        d = lognormal_ms(self.rng, mean, self.ccfg.service_cv)
        self.db_ms[i] = d
        self._push(now + d / 1000, DB_DONE, i)

    def _on_app_done(self, i: int, now: float) -> None:
        self.app_busy -= 1
        if self.db_free > 0:
            self.db_free -= 1
            self._start_db(i, now)
        else:
            self.db_q.append(i)          # keeps holding its worker

    def _on_db_done(self, i: int, now: float) -> None:
        self.db_active -= 1
        self.t_done[i] = now
        lat = (now - self.t_admit[i]) * 1000
        self.core.on_complete(now, lat)
        self._win_counts["completed"] += 1
        self._win_lat.append(lat)
        if self.db_q:
            self._start_db(self.db_q.popleft(), now)
        else:
            self.db_free += 1
        if self.worker_q:
            self._start_app(self.worker_q.popleft(), now)
        else:
            self.workers_free += 1
            self.busy -= 1
        self._dispatch(now)

    # ============================================================== metrics
    def _finalize(self, t_final: float, peak_rate: float, aborted: bool, reason: str) -> dict:
        c = self.ccfg
        m = (self.t_arr >= self.warmup_s) & (self.t_arr <= t_final)
        st = self.status[m]
        arr, adm, done = self.t_arr[m], self.t_admit[m], self.t_done[m]
        n_arr = int(m.sum())

        admitted = st == ADMITTED
        lat = np.where(np.isnan(done), t_final - adm, done - adm)[admitted] * 1000  # censored if unfinished
        finished = ~np.isnan(done[admitted])
        errors = (lat > c.timeout_ms)
        success = finished & ~errors

        wait = np.concatenate([
            (adm - arr)[admitted],
            (t_final - arr)[st == QUEUED],
            np.full(int((st == EXPIRED).sum()), self.scfg.max_wait_s),
        ]) * 1000
        meas_s = max(min(self.t_end, t_final) - self.warmup_s, 1e-9)
        dur = max(t_final, 1e-9)

        def pct(a, q):
            return float(np.percentile(a, q)) if len(a) else 0.0

        db = self.db_ms[m][admitted]
        db = db[~np.isnan(db)]
        return {
            "L95_ms": pct(lat, 95),
            "L99_ms": pct(lat, 99),
            "L50_ms": pct(lat, 50),
            "throughput_rps": float(success.sum() / meas_s),
            "error_rate": float(errors.mean()) if len(errors) else 0.0,
            "rejection_rate": float(np.isin(st, (REJECTED, EXPIRED)).sum() / max(n_arr, 1)),
            "Qw_mean_ms": float(wait.mean()) if len(wait) else 0.0,
            "Qw95_ms": pct(wait, 95),
            "Qw_max_ms": float(wait.max()) if len(wait) else 0.0,
            "queue_max": int(self.core.room.max_length_seen),
            "cpu_util": float(self.cpu_area / (self.n_workers * dur)),
            "worker_util": float(self.busy_area / (self.n_workers * dur)),
            "db_util": float(self.db_area / (c.db_pool_size * dur)),
            "db_write_mean_ms": float(db.mean()) if len(db) else 0.0,
            "arrivals": n_arr,
            "admitted": int(admitted.sum()),
            "rejected": int((st == REJECTED).sum()),
            "expired": int((st == EXPIRED).sum()),
            "aborted": bool(aborted),
            "abort_reason": reason,
            "sim_time_s": float(t_final),
        }
