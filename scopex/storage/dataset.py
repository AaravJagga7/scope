"""Dataset generation using Latin Hypercube and Grid sampling with train/held-out partitioning."""

from __future__ import annotations

import datetime
import math
import random
import uuid
from typing import Any

import numpy as np
from scipy.stats import qmc

from scopex.storage.config import ExperimentConfig, WorkloadSpec
from scopex.storage.repository import (
    ExperimentRepository,
    ExperimentRun,
    get_current_git_commit,
)

WORKLOAD_PROFILES = [
    "normal",
    "flash_sale",
    "ramp",
    "viral_product",
    "slow_payment_api",
]


def generate_lhs_configs(
    n: int = 120,
    held_out_ratio: float = 0.20,
    seed: int = 42,
) -> list[ExperimentConfig]:
    """Generates n configurations using Latin Hypercube Sampling across the parameter space.

    Dimensions sampled:
    0: traffic RPS in [50, 1000]
    1: workload profile categorical in WORKLOAD_PROFILES
    2: checkout replicas in [1, 10]
    3: db_pool_size in [5, 100]
    4: cache_enabled boolean [False, True]
    """
    if n <= 0:
        raise ValueError(f"n must be positive, got {n}")
    if not (0.0 < held_out_ratio < 1.0):
        raise ValueError(f"held_out_ratio must be between 0 and 1, got {held_out_ratio}")

    sampler = qmc.LatinHypercube(d=5, seed=seed)
    samples = sampler.random(n=n)

    # Determine held-out split assignments deterministically
    held_out_count = int(round(n * held_out_ratio))
    rng = np.random.default_rng(seed)
    indices = np.arange(n)
    rng.shuffle(indices)
    held_out_indices = set(indices[:held_out_count])

    configs: list[ExperimentConfig] = []
    for i in range(n):
        s = samples[i]
        # Dim 0: RPS from 50 to 1000 (quantized to nearest 25)
        raw_rps = 50 + s[0] * (1000 - 50)
        rps = int(round(raw_rps / 25.0) * 25)
        rps = max(50, min(1000, rps))

        # Dim 1: profile
        profile_idx = min(int(s[1] * len(WORKLOAD_PROFILES)), len(WORKLOAD_PROFILES) - 1)
        profile = WORKLOAD_PROFILES[profile_idx]

        # Dim 2: checkout replicas [1, 10]
        replicas = int(1 + s[2] * 9.99)
        replicas = max(1, min(10, replicas))

        # Dim 3: db_pool_size [5, 100]
        raw_db = 5 + s[3] * (100 - 5)
        db_pool = int(round(raw_db / 5.0) * 5)
        db_pool = max(5, min(100, db_pool))

        # Dim 4: cache_enabled
        cache_enabled = bool(s[4] >= 0.5)

        split = "held_out" if i in held_out_indices else "train"
        exp_id = f"exp_lhs_{uuid.uuid4().hex[:8]}"

        w_spec = WorkloadSpec(
            rps=rps,
            profile=profile,
            duration=15,
            warmup_duration=2,
        )
        cfg = ExperimentConfig(
            workload=w_spec,
            checkout_replicas=replicas,
            db_pool_size=db_pool,
            cpu_limit="500m",
            cache_enabled=cache_enabled,
            consumers=2,
            seed=seed + i,
            split=split,
            experiment_id=exp_id,
            tags={"sampling": "lhs", "split": split, "sample_idx": str(i)},
            description=f"LHS experiment {i + 1}/{n} ({profile}, {rps} RPS, {replicas} reps, pool={db_pool}, cache={cache_enabled})",
        )
        cfg.validate()
        configs.append(cfg)

    return configs


def generate_grid_configs(
    held_out_ratio: float = 0.20,
    seed: int = 42,
) -> list[ExperimentConfig]:
    """Generates a structured grid of configurations over key operating parameters."""
    rps_values = [100, 250, 500, 800]
    profiles = ["normal", "flash_sale", "viral_product"]
    replicas_values = [1, 2, 4, 8]
    pool_values = [10, 25, 50, 80]
    cache_values = [False, True]

    # Grid size: 4 * 3 * 4 * 4 * 2 = 384 configurations
    raw_combos = []
    for rps in rps_values:
        for prof in profiles:
            for rep in replicas_values:
                for pool in pool_values:
                    for cache in cache_values:
                        raw_combos.append((rps, prof, rep, pool, cache))

    n = len(raw_combos)
    held_out_count = int(round(n * held_out_ratio))
    rng = np.random.default_rng(seed)
    indices = np.arange(n)
    rng.shuffle(indices)
    held_out_indices = set(indices[:held_out_count])

    configs: list[ExperimentConfig] = []
    for i, (rps, prof, rep, pool, cache) in enumerate(raw_combos):
        split = "held_out" if i in held_out_indices else "train"
        exp_id = f"exp_grid_{uuid.uuid4().hex[:8]}"

        w_spec = WorkloadSpec(
            rps=rps,
            profile=prof,
            duration=15,
            warmup_duration=2,
        )
        cfg = ExperimentConfig(
            workload=w_spec,
            checkout_replicas=rep,
            db_pool_size=pool,
            cpu_limit="500m",
            cache_enabled=cache,
            consumers=2,
            seed=seed + i,
            split=split,
            experiment_id=exp_id,
            tags={"sampling": "grid", "split": split, "grid_idx": str(i)},
            description=f"Grid experiment {i + 1}/{n}",
        )
        cfg.validate()
        configs.append(cfg)

    return configs


def simulate_system_metrics(
    config: ExperimentConfig,
    seed: int | None = None,
) -> dict[str, Any]:
    """Simulates realistic microservice performance metrics based on queuing and capacity dynamics.

    Models:
    - Frontend CPU capacity per checkout replica.
    - Database connection pool capacity with cache mitigation.
    - Queuing delay and response time inflation near saturation.
    - Active DB connections and error rate upon pool exhaustion.
    """
    effective_seed = seed if seed is not None else config.seed
    rng = random.Random(effective_seed)

    target_rps = float(config.workload.rps)
    replicas = float(config.checkout_replicas)
    pool_size = float(config.db_pool_size)
    cache = config.cache_enabled
    profile = config.workload.profile

    # 1. Frontend CPU Capacity
    frontend_rps_per_replica = 70.0
    if profile == "slow_payment_api":
        frontend_rps_per_replica = 35.0  # Slow external payment blocks workers
    frontend_capacity = replicas * frontend_rps_per_replica

    # 2. Database Capacity
    # Base query rate per request: ~1.2 queries in checkout flow
    # Cache offloads product lookups:
    if cache:
        if profile == "viral_product":
            db_factor = 5.0  # 90% cache hits on viral item
        else:
            db_factor = 2.2  # 60% cache hits on general catalog
    else:
        db_factor = 1.0

    db_rps_capacity = pool_size * 16.0 * db_factor

    # Effective bottleneck capacity
    system_capacity = min(frontend_capacity, db_rps_capacity)

    # Utilization rho
    rho = target_rps / max(1.0, system_capacity)

    # Random noise factor
    noise = rng.gauss(0.0, 0.02)

    # Throughput
    if rho <= 1.0:
        achieved_rps = target_rps * (1.0 + noise)
    else:
        achieved_rps = system_capacity * (1.0 + noise)
    achieved_rps = max(1.0, round(achieved_rps, 2))

    # Base latency by profile
    base_p50 = 12.0
    base_p95 = 26.0
    base_p99 = 45.0

    if profile == "slow_payment_api":
        base_p50 += 120.0
        base_p95 += 180.0
        base_p99 += 240.0
    elif profile == "flash_sale":
        base_p50 += 8.0
        base_p95 += 20.0
        base_p99 += 35.0
    elif profile == "viral_product" and not cache:
        base_p50 += 15.0
        base_p95 += 40.0
        base_p99 += 70.0

    if cache:
        base_p50 = max(4.0, base_p50 - 5.0)
        base_p95 = max(8.0, base_p95 - 8.0)
        base_p99 = max(15.0, base_p99 - 10.0)

    # Queuing effect: exponential increase as rho approaches 1.0
    queue_factor = 1.0 / max(0.04, 1.02 - min(0.98, rho))
    p50 = round(base_p50 * math.sqrt(queue_factor) * (1.0 + rng.gauss(0.0, 0.03)), 1)
    p95 = round(base_p95 * queue_factor * (1.0 + rng.gauss(0.0, 0.04)), 1)
    p99 = round(base_p99 * (queue_factor**1.2) * (1.0 + rng.gauss(0.0, 0.05)), 1)

    # Error rate
    if rho <= 1.02:
        error_rate_pct = 0.0
    else:
        # Connection pool timeouts or queue drop errors
        err_fraction = (target_rps - system_capacity) / target_rps
        error_rate_pct = round(min(75.0, err_fraction * 100.0 * (1.0 + rng.gauss(0.0, 0.05))), 2)

    # Active DB Connections
    db_conns = min(pool_size, round(replicas * 6.0 * min(1.2, rho) + rng.uniform(-0.5, 0.5), 1))
    db_conns = max(1.0, min(pool_size, db_conns))

    # CPU Percentage
    cpu_pct = min(
        98.0,
        round(
            20.0
            + 75.0 * min(1.0, achieved_rps / max(1.0, frontend_capacity))
            + rng.gauss(0.0, 1.0),
            1,
        ),
    )
    cpu_pct = max(10.0, cpu_pct)

    return {
        "profile": profile,
        "target_rps": int(target_rps),
        "achieved_rps": achieved_rps,
        "total_requests": int(achieved_rps * config.workload.duration),
        "error_count": int((error_rate_pct / 100.0) * (achieved_rps * config.workload.duration)),
        "error_rate_percent": error_rate_pct,
        "client_side_latency_percentiles": {
            "p50": p50,
            "p90": round(p95 * 0.85, 1),
            "p95": p95,
            "p99": p99,
            "max": round(p99 * 1.3, 1),
        },
        "db_connections": db_conns,
        "cpu_pct": cpu_pct,
        "mode": "synthetic_simulation",
    }


def generate_and_store_dataset(
    n: int = 120,
    method: str = "lhs",
    held_out_ratio: float = 0.20,
    seed: int = 42,
    repository: ExperimentRepository | None = None,
) -> list[ExperimentRun]:
    """Generates an empirical dataset of labelled experiments, partitioned into train and held-out sets,

    and persists them to SQLite and Parquet.
    """
    repo = repository or ExperimentRepository()
    now_iso = datetime.datetime.now(datetime.UTC).isoformat()
    git_commit = get_current_git_commit()

    if method.lower() == "grid":
        configs = generate_grid_configs(held_out_ratio=held_out_ratio, seed=seed)
        if n < len(configs):
            # Select subset if n is smaller than full grid
            configs = configs[:n]
    else:
        configs = generate_lhs_configs(n=n, held_out_ratio=held_out_ratio, seed=seed)

    runs: list[ExperimentRun] = []
    for cfg in configs:
        metrics = simulate_system_metrics(cfg, seed=cfg.seed)
        lats = metrics["client_side_latency_percentiles"]

        run = ExperimentRun(
            experiment_id=cfg.experiment_id,
            run_id=f"run_{uuid.uuid4().hex[:10]}",
            group_name=cfg.tags.get("group", method.upper()),
            split=cfg.split,
            status="COMPLETED",
            git_commit=git_commit,
            created_at=now_iso,
            started_at=now_iso,
            completed_at=now_iso,
            config=cfg,
            metrics=metrics,
            throughput_rps=metrics["achieved_rps"],
            p50_latency_ms=lats["p50"],
            p95_latency_ms=lats["p95"],
            p99_latency_ms=lats["p99"],
            error_rate_pct=metrics["error_rate_percent"],
            db_connections=metrics["db_connections"],
            cpu_pct=metrics["cpu_pct"],
        )
        runs.append(run)

    repo.save_runs_batch(runs)
    return runs
