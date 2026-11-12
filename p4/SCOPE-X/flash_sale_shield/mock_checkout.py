"""Mock Checkout service (single-process stand-in until the team's testbed).

Service model (shared by the live asyncio version below and by the
discrete-event testbed in ``experiment_engine.simulator``):

1. wait for a free Checkout worker   (replicas * workers_per_replica slots)
2. app phase  ~ LogNormal(mean=app_time_ms)                 (holds worker)
3. wait for a connection from the shared DB pool            (holds worker)
4. DB write   ~ LogNormal(mean=db_write_ms * (1 + c*active/pool))
5. release DB connection and worker

Inventory consistency is not modelled as a failure mode here: the real
Inventory Service enforces it transactionally and experiments must never
violate it deliberately.
"""
from __future__ import annotations

import asyncio
import math
import random
import time

from .config import CheckoutConfig


def lognormal_ms(rng: random.Random, mean_ms: float, cv: float) -> float:
    """Sample a lognormal with the given mean and coefficient of variation."""
    if cv <= 0:
        return mean_ms
    sigma2 = math.log(1 + cv * cv)
    mu = math.log(mean_ms) - sigma2 / 2
    return rng.lognormvariate(mu, math.sqrt(sigma2))


def db_write_mean_ms(cfg: CheckoutConfig, active_connections: int) -> float:
    return cfg.db_write_ms * (1 + cfg.db_contention * active_connections / cfg.db_pool_size)


class AsyncMockCheckout:
    """Live (wall-clock) version used behind the FastAPI shield."""

    def __init__(self, cfg: CheckoutConfig | None = None, seed: int | None = None):
        self.cfg = cfg or CheckoutConfig()
        self.rng = random.Random(seed)
        self._workers = asyncio.Semaphore(self.cfg.replicas * self.cfg.workers_per_replica)
        self._db = asyncio.Semaphore(self.cfg.db_pool_size)
        self.db_active = 0
        self.busy_workers = 0

    async def process(self) -> dict:
        t0 = time.perf_counter()
        async with self._workers:
            self.busy_workers += 1
            try:
                await asyncio.sleep(lognormal_ms(self.rng, self.cfg.app_time_ms, self.cfg.service_cv) / 1000)
                t_db_wait = time.perf_counter()
                async with self._db:
                    self.db_active += 1
                    try:
                        mean = db_write_mean_ms(self.cfg, self.db_active)
                        t_db = time.perf_counter()
                        await asyncio.sleep(lognormal_ms(self.rng, mean, self.cfg.service_cv) / 1000)
                        db_write_ms = (time.perf_counter() - t_db) * 1000
                    finally:
                        self.db_active -= 1
            finally:
                self.busy_workers -= 1
        latency_ms = (time.perf_counter() - t0) * 1000
        return {
            "latency_ms": latency_ms,
            "db_wait_ms": (t_db - t_db_wait) * 1000,
            "db_write_ms": db_write_ms,
            "ok": latency_ms <= self.cfg.timeout_ms,
        }
