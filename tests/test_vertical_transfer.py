"""Tests for Task 14D: constraints under vertical scaling."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from scopex.discovery.vertical_transfer import (
    BASELINE_CPU_CORES,
    BASELINE_RPS_PER_REPLICA,
    VerticalStudyDesign,
    add_derived_columns,
    capacity_observations,
    cpu_capacity,
    fit_capacity_power_law,
    gc_capacity_factor,
    generating_alpha,
    simulate_vertical_run,
    throttled_periods_pct,
)


def test_capacity_calibrated_to_static_baseline():
    assert math.isclose(cpu_capacity(BASELINE_CPU_CORES), BASELINE_RPS_PER_REPLICA, rel_tol=1e-9)


def test_capacity_is_sublinear_in_cpu():
    assert cpu_capacity(2.0) < 4 * cpu_capacity(0.5)
    assert 0.0 < generating_alpha([0.5, 1.0, 2.0]) < 1.0


def test_throttling_and_gc_monotone():
    assert throttled_periods_pct(0.9, 0.5) >= throttled_periods_pct(0.9, 2.0)
    assert throttled_periods_pct(0.3, 0.5) == 0.0
    assert gc_capacity_factor(0.5) == 1.0
    assert gc_capacity_factor(1.0) < 1.0


def test_power_law_fit_recovers_known_exponent():
    rng = np.random.default_rng(0)
    rows = []
    for cpu in (0.5, 1.0, 2.0):
        for mem in (512, 2048):
            for _ in range(20):
                rows.append(
                    {
                        "run_id": f"r{len(rows)}",
                        "cpu_cores": cpu,
                        "memory_mib": mem,
                        "checkout_replicas": 2,
                        "capacity_rps": 100.0 * cpu**0.6 * math.exp(rng.normal(0, 0.02)),
                    }
                )
    fit = fit_capacity_power_law(pd.DataFrame(rows), n_boot=50)
    assert fit["estimable"]
    lo, hi = fit["alpha_ci95"]
    assert lo <= 0.6 <= hi
    assert fit["linear_scaling_test"]["rejected_at_5pct"]


def test_power_law_fit_needs_two_cpu_levels():
    one_level = pd.DataFrame(
        [
            {
                "run_id": "a",
                "cpu_cores": 1.0,
                "memory_mib": 512,
                "checkout_replicas": 2,
                "capacity_rps": 100.0,
            }
        ]
    )
    assert fit_capacity_power_law(one_level)["estimable"] is False


def test_vertical_run_records_restarts_and_throttling():
    design = VerticalStudyDesign(duration=20)
    overloaded = {
        "cpu_cores": 0.5,
        "memory_mib": 512,
        "checkout_replicas": 2,
        "db_pool_size": 100,
        "cache_enabled": True,
        "target_rps": 1000,
    }
    summary, frame = simulate_vertical_run(overloaded, design)
    assert summary["pod_restarts"] > 0, "512Mi under heavy overload should OOM"
    assert (frame["phase"] == "transition").any()
    assert frame["throttled_periods_pct"].max() > 0

    roomy = dict(overloaded, memory_mib=2048, target_rps=150, cpu_cores=2.0)
    summary2, _ = simulate_vertical_run(roomy, design)
    assert summary2["pod_restarts"] == 0


def test_capacity_observations_only_use_cpu_saturated_steady_seconds():
    design = VerticalStudyDesign(duration=20)
    cell = {
        "cpu_cores": 1.0,
        "memory_mib": 2048,
        "checkout_replicas": 2,
        "db_pool_size": 100,
        "cache_enabled": True,
        "target_rps": 800,
    }
    _, frame = simulate_vertical_run(cell, design)
    obs = capacity_observations(frame)
    assert len(obs) == 1
    assert math.isclose(obs["capacity_rps"].iloc[0], cpu_capacity(1.0), rel_tol=0.1)


def test_derived_columns():
    df = add_derived_columns(
        pd.DataFrame([{"target_rps": 300, "checkout_replicas": 3, "db_pool_size": 60}])
    )
    assert df["rps_per_replica"].iloc[0] == 100
    assert df["rps_per_pool_conn"].iloc[0] == 5
    assert df["cpu_cores"].iloc[0] == BASELINE_CPU_CORES
