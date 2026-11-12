"""Runs one experiment x = (lambda, r, a, K, B) and returns a measurement row.

Backends
  sim     discrete-event testbed surrogate (fast, reproducible)  [default]
  inproc  real asyncio shield + mock Checkout on the wall clock (slow;
          used to check the simulator against the live code path)
  k8s     placeholder for the team's Kubernetes testbed (Milestone 5)

CLI examples
  python -m experiment_engine.experiment_runner single --traffic 500 --replicas 2 --admission 300
  python -m experiment_engine.experiment_runner design --kind random --n 40 --out datasets/experiments.csv
"""
from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import time
from typing import Dict, Optional, Sequence

import numpy as np

from flash_sale_shield.config import CheckoutConfig, ShieldConfig

from .data_collector import DataCollector
from .experiment_config import (DEFAULT_PROFILE, DEFAULT_SAFETY, DEFAULT_SLO, DEFAULT_SPACE,
                                SafetyLimits, SearchSpace, SLO, WorkloadProfile)
from .simulator import Simulator
from .workload_generator import arrival_times

_counter = itertools.count(1)


def configs_from_x(x: Dict[str, float], base_shield: Optional[ShieldConfig] = None,
                   base_checkout: Optional[CheckoutConfig] = None,
                   shield_override: Optional[Dict] = None):
    """x sets (a, K, B, r); ``shield_override`` is applied last (e.g. to
    switch the shield off for a no-shield baseline)."""
    s = ShieldConfig(**(base_shield or ShieldConfig()).to_dict())
    s.update(admission_rate=x["admission_rate"], queue_capacity=int(x["queue_capacity"]),
             bucket_capacity=int(x["bucket_capacity"]))
    if shield_override:
        s.update(**shield_override)
    c = CheckoutConfig(**(base_checkout or CheckoutConfig()).to_dict())
    c.replicas = int(x["replicas"])
    return s, c


def run_experiment(x: Dict[str, float], seed: int = 0, backend: str = "sim",
                   profile: WorkloadProfile = DEFAULT_PROFILE,
                   safety: SafetyLimits = DEFAULT_SAFETY, slo: SLO = DEFAULT_SLO,
                   strategy: str = "manual", return_timeseries: bool = False,
                   base_shield: Optional[ShieldConfig] = None,
                   base_checkout: Optional[CheckoutConfig] = None,
                   shield_override: Optional[Dict] = None):
    shield_cfg, checkout_cfg = configs_from_x(x, base_shield, base_checkout, shield_override)
    if backend == "sim":
        res = Simulator(shield_cfg, checkout_cfg, profile, safety, seed).run(x["traffic_rate"])
        metrics, ts = res.metrics, res.timeseries
    elif backend == "inproc":
        metrics, ts = asyncio.run(_run_inproc(x, shield_cfg, checkout_cfg, profile, seed)), {}
    elif backend == "k8s":
        raise NotImplementedError("Kubernetes backend is wired in Milestone 5 (team testbed).")
    else:
        raise ValueError(f"unknown backend {backend!r}")

    row = {"experiment_id": next(_counter), "strategy": strategy, "seed": seed,
           "backend": backend, **{k: x[k] for k in x}, **metrics}
    row["slo_ok"] = slo.satisfied(row)
    return (row, ts) if return_timeseries else row


async def _run_inproc(x, shield_cfg, checkout_cfg, profile, seed) -> Dict:
    """Real-time run through flash_sale_shield.runtime.ShieldRuntime."""
    from flash_sale_shield.runtime import ShieldRuntime

    rt = ShieldRuntime(shield_cfg, checkout_cfg, seed=seed, window=10 ** 6)
    rt.start()
    times = arrival_times(x["traffic_rate"], profile, np.random.default_rng(seed))
    results, tasks = [], []
    t0 = time.perf_counter()

    async def one():
        results.append(await rt.submit())

    for t in times:
        delay = t - (time.perf_counter() - t0)
        if delay > 0:
            await asyncio.sleep(delay)
        tasks.append(asyncio.create_task(one()))
    await asyncio.gather(*tasks)
    await rt.stop()
    lat = np.array([r["latency_ms"] for r in results if "latency_ms" in r])
    waits = np.array([r["wait_ms"] for r in results if "wait_ms" in r])
    rej = sum(1 for r in results if r["status"] == 503)
    ok = sum(1 for r in results if r["status"] == 200)
    total = profile.pre_s + profile.burst_s + profile.post_s
    return {
        "L95_ms": float(np.percentile(lat, 95)) if len(lat) else 0.0,
        "L99_ms": float(np.percentile(lat, 99)) if len(lat) else 0.0,
        "L50_ms": float(np.percentile(lat, 50)) if len(lat) else 0.0,
        "throughput_rps": ok / total,
        "error_rate": float((lat > checkout_cfg.timeout_ms).mean()) if len(lat) else 0.0,
        "rejection_rate": rej / max(len(results), 1),
        "Qw_mean_ms": float(waits.mean()) if len(waits) else 0.0,
        "Qw95_ms": float(np.percentile(waits, 95)) if len(waits) else 0.0,
        "Qw_max_ms": float(waits.max()) if len(waits) else 0.0,
        "queue_max": rt.core.room.max_length_seen,
        "cpu_util": float("nan"), "db_util": float("nan"), "db_write_mean_ms": float("nan"),
        "arrivals": len(results), "admitted": rt.core.admitted, "rejected": rt.core.room.rejected_full,
        "expired": rt.core.room.expired, "aborted": False, "abort_reason": "",
        "sim_time_s": time.perf_counter() - t0,
    }


def run_design(X: np.ndarray, space: SearchSpace = DEFAULT_SPACE, seed0: int = 0,
               strategy: str = "design", backend: str = "sim",
               collector: Optional[DataCollector] = None, **kw):
    rows = []
    for i, x in enumerate(X):
        row = run_experiment(space.as_dict(x), seed=seed0 + i, backend=backend, strategy=strategy, **kw)
        rows.append(row)
        if collector:
            collector.append(row)
    return rows


def _cli():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("single")
    s.add_argument("--traffic", type=float, default=500)
    s.add_argument("--replicas", type=int, default=2)
    s.add_argument("--admission", type=float, default=300)
    s.add_argument("--queue", type=int, default=500)
    s.add_argument("--bucket", type=int, default=100)
    s.add_argument("--seed", type=int, default=0)
    s.add_argument("--backend", default="sim", choices=["sim", "inproc", "k8s"])
    d = sub.add_parser("design")
    d.add_argument("--kind", default="random", choices=["random", "grid"])
    d.add_argument("--n", type=int, default=40)
    d.add_argument("--seed", type=int, default=0)
    d.add_argument("--out", default="datasets/experiments.csv")
    a = p.parse_args()

    if a.cmd == "single":
        x = {"traffic_rate": a.traffic, "replicas": a.replicas, "admission_rate": a.admission,
             "queue_capacity": a.queue, "bucket_capacity": a.bucket}
        print(json.dumps(run_experiment(x, a.seed, a.backend), indent=2))
    else:
        space = DEFAULT_SPACE
        X = space.grid() if a.kind == "grid" else space.sample(a.n, np.random.default_rng(a.seed))
        rows = run_design(X, space, a.seed * 10000, a.kind, collector=DataCollector(a.out))
        print(f"wrote {len(rows)} rows to {a.out}")


if __name__ == "__main__":
    _cli()
