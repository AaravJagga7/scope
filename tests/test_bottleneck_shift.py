"""Unit tests for bottleneck shifting experiment runner and data generation."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from experiments.bottleneck_shift import (
    generate_bottleneck_plot,
    run_bottleneck_experiment,
    sample_cpu_percent,
    sample_db_connections,
    save_results_to_csv,
)


def test_sample_db_connections() -> None:
    """Verifies database connection modeling respects pool limits."""
    # Low load: below limit
    conns_low = sample_db_connections(db_pool_size=50, replicas=1, throughput=90.0, is_mock=True)
    assert conns_low < 50.0

    # High load: capped at db_pool_size
    conns_high = sample_db_connections(db_pool_size=50, replicas=8, throughput=450.0, is_mock=True)
    assert conns_high <= 50.0


def test_sample_cpu_percent() -> None:
    """Verifies CPU utilization sampling returns bounded percentages."""
    cpu = sample_cpu_percent(replicas=4, is_mock=True)
    assert 0.0 <= cpu <= 100.0


def test_save_results_to_csv(tmp_path: Path) -> None:
    """Verifies that experiment results are cleanly exported to CSV."""
    csv_file = tmp_path / "test_results.csv"
    dummy_data: list[dict[str, Any]] = [
        {
            "replicas": 1,
            "db_pool_size": 50,
            "throughput_rps": 95.0,
            "p50_latency_ms": 18.0,
            "p95_latency_ms": 32.0,
            "p99_latency_ms": 48.0,
            "error_count": 0,
            "error_rate_pct": 0.0,
            "db_connections": 12.0,
            "cpu_pct": 94.0,
            "timestamp": "2026-11-12T00:00:00Z",
        },
        {
            "replicas": 2,
            "db_pool_size": 50,
            "throughput_rps": 190.0,
            "p50_latency_ms": 22.0,
            "p95_latency_ms": 38.0,
            "p99_latency_ms": 58.0,
            "error_count": 0,
            "error_rate_pct": 0.0,
            "db_connections": 24.0,
            "cpu_pct": 88.0,
            "timestamp": "2026-11-12T00:01:00Z",
        },
    ]

    save_results_to_csv(dummy_data, csv_file)
    assert csv_file.exists()

    with open(csv_file) as fp:
        reader = csv.DictReader(fp)
        rows = list(reader)
        assert len(rows) == 2
        assert rows[0]["replicas"] == "1"
        assert rows[1]["throughput_rps"] == "190.0"


def test_generate_bottleneck_plot(tmp_path: Path) -> None:
    """Verifies that plot generation creates a valid image file."""
    plot_file = tmp_path / "test_plot.png"
    dummy_data: list[dict[str, Any]] = [
        {
            "replicas": 1,
            "throughput_rps": 100.0,
            "p95_latency_ms": 30.0,
            "db_connections": 10.0,
        },
        {
            "replicas": 2,
            "throughput_rps": 200.0,
            "p95_latency_ms": 40.0,
            "db_connections": 20.0,
        },
        {
            "replicas": 4,
            "throughput_rps": 350.0,
            "p95_latency_ms": 60.0,
            "db_connections": 40.0,
        },
        {
            "replicas": 6,
            "throughput_rps": 390.0,
            "p95_latency_ms": 200.0,
            "db_connections": 50.0,
        },
        {
            "replicas": 8,
            "throughput_rps": 395.0,
            "p95_latency_ms": 450.0,
            "db_connections": 50.0,
        },
    ]

    generate_bottleneck_plot(dummy_data, db_pool_size=50, output_path=plot_file)
    assert plot_file.exists()
    assert plot_file.stat().st_size > 1000  # Non-empty PNG


def test_run_bottleneck_experiment_quick(tmp_path: Path) -> None:
    """Integration test running bottleneck experiment on 2 replica settings."""
    out_csv = tmp_path / "quick.csv"
    out_plot = tmp_path / "quick.png"

    results = run_bottleneck_experiment(
        replica_list=[1, 2],
        db_pool_size=50,
        target_rps=100,
        duration=1,
        warmup_duration=1,
        output_csv=out_csv,
        output_plot=out_plot,
        mock_mode=True,
    )

    assert len(results) == 2
    assert out_csv.exists()
    assert out_plot.exists()
    assert results[0]["replicas"] == 1
    assert results[1]["replicas"] == 2
