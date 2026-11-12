"""Unit and integration tests for scopex/storage and experiment runner."""

from __future__ import annotations

from pathlib import Path

import pytest

from scopex.storage import (
    ExperimentConfig,
    ExperimentRepository,
    ExperimentRun,
    ExperimentRunner,
    SafetyLimits,
    WorkloadSpec,
)


def test_workload_spec_validation() -> None:
    """Verifies WorkloadSpec parameter validation."""
    valid_spec = WorkloadSpec(rps=100, profile="normal", duration=10)
    valid_spec.validate()

    with pytest.raises(ValueError, match="rps must be positive"):
        WorkloadSpec(rps=-10).validate()

    with pytest.raises(ValueError, match="Invalid profile"):
        WorkloadSpec(rps=100, profile="invalid_profile").validate()


def test_experiment_config_serialization() -> None:
    """Verifies serialization round-trip to dict and JSON."""
    cfg = ExperimentConfig(
        workload=WorkloadSpec(rps=200, profile="flash_sale", duration=15),
        checkout_replicas=4,
        db_pool_size=50,
        cpu_limit="500m",
        cache_enabled=True,
        consumers=2,
        seed=42,
        split="train",
    )
    cfg.validate()

    # Dictionary round-trip
    d = cfg.to_dict()
    reconstructed = ExperimentConfig.from_dict(d)
    assert reconstructed.workload.rps == 200
    assert reconstructed.checkout_replicas == 4
    assert reconstructed.split == "train"

    # JSON round-trip
    j = cfg.to_json()
    from_j = ExperimentConfig.from_json(j)
    assert from_j.workload.profile == "flash_sale"
    assert from_j.db_pool_size == 50


def test_safety_limits_enforcement() -> None:
    """Verifies that resource ceilings prevent dangerous configurations."""
    safety = SafetyLimits(max_rps=1000, max_replicas=16, max_db_pool_size=100)

    # Exceeding max RPS
    unsafe_rps = ExperimentConfig(
        workload=WorkloadSpec(rps=2000),
        checkout_replicas=2,
        db_pool_size=50,
    )
    with pytest.raises(ValueError, match="Safety Violation: target RPS"):
        safety.check_safety(unsafe_rps)

    # Exceeding max replicas
    unsafe_replicas = ExperimentConfig(
        workload=WorkloadSpec(rps=100),
        checkout_replicas=32,
        db_pool_size=50,
    )
    with pytest.raises(ValueError, match="Safety Violation: checkout replicas"):
        safety.check_safety(unsafe_replicas)

    # Exceeding max db_pool_size
    unsafe_pool = ExperimentConfig(
        workload=WorkloadSpec(rps=100),
        checkout_replicas=2,
        db_pool_size=200,
    )
    with pytest.raises(ValueError, match="Safety Violation: db_pool_size"):
        safety.check_safety(unsafe_pool)


def test_sqlite_and_parquet_repository(tmp_path: Path) -> None:
    """Verifies persistence in SQLite and export to Parquet."""
    db_file = tmp_path / "test_repo.db"
    pq_file = tmp_path / "test_repo.parquet"
    repo = ExperimentRepository(db_path=db_file, parquet_path=pq_file)

    cfg_train = ExperimentConfig(
        workload=WorkloadSpec(rps=50, profile="normal"),
        checkout_replicas=2,
        db_pool_size=50,
        split="train",
        experiment_id="exp_001",
    )
    cfg_heldout = ExperimentConfig(
        workload=WorkloadSpec(rps=150, profile="flash_sale"),
        checkout_replicas=4,
        db_pool_size=50,
        split="held_out",
        experiment_id="exp_002",
    )

    run1 = ExperimentRun(
        experiment_id="exp_001",
        run_id="run_1",
        split="train",
        status="COMPLETED",
        git_commit="test_commit_hash",
        created_at="2026-11-12T00:00:00Z",
        started_at="2026-11-12T00:00:01Z",
        completed_at="2026-11-12T00:00:05Z",
        config=cfg_train,
        throughput_rps=48.5,
        p50_latency_ms=15.0,
        p95_latency_ms=35.0,
        p99_latency_ms=50.0,
        error_rate_pct=0.0,
        db_connections=10.0,
        cpu_pct=45.0,
    )

    run2 = ExperimentRun(
        experiment_id="exp_002",
        run_id="run_2",
        split="held_out",
        status="COMPLETED",
        git_commit="test_commit_hash",
        created_at="2026-11-12T00:01:00Z",
        started_at="2026-11-12T00:01:01Z",
        completed_at="2026-11-12T00:01:05Z",
        config=cfg_heldout,
        throughput_rps=145.0,
        p50_latency_ms=20.0,
        p95_latency_ms=45.0,
        p99_latency_ms=65.0,
        error_rate_pct=0.0,
        db_connections=25.0,
        cpu_pct=65.0,
    )

    repo.save_run(run1)
    repo.save_run(run2)

    # Verify SQLite queries
    train_runs = repo.list_runs(split="train")
    assert len(train_runs) == 1
    assert train_runs[0].experiment_id == "exp_001"
    assert train_runs[0].throughput_rps == 48.5

    held_out_runs = repo.list_runs(split="held_out")
    assert len(held_out_runs) == 1
    assert held_out_runs[0].experiment_id == "exp_002"

    # Verify Parquet export
    assert pq_file.exists()
    df = repo.to_dataframe()
    assert len(df) == 2
    assert "git_commit" in df.columns
    assert "throughput_rps" in df.columns
    assert "p95_latency_ms" in df.columns
    assert set(df["split"]) == {"train", "held_out"}


def test_acceptance_reproducibility_within_noise(tmp_path: Path) -> None:
    """Acceptance Test: running the same config twice produces reproducible results within noise."""
    db_file = tmp_path / "acceptance_test.db"
    pq_file = tmp_path / "acceptance_test.parquet"
    repo = ExperimentRepository(db_path=db_file, parquet_path=pq_file)
    runner = ExperimentRunner(repository=repo, mock_mode=True)

    config = ExperimentConfig(
        workload=WorkloadSpec(rps=100, profile="normal", duration=2, warmup_duration=1),
        checkout_replicas=2,
        db_pool_size=50,
        split="train",
        seed=42,
    )

    # Execute 2 repeated runs
    result = runner.run_repeated(config, repeats=2)

    assert result.repeats == 2
    assert len(result.runs) == 2

    # Verify mean and standard deviation are computed
    assert result.throughput_mean > 0
    assert result.p95_mean > 0
    assert result.throughput_std >= 0
    assert result.p95_std >= 0

    # Verify reproducibility within noise (CV < 15% for throughput)
    cv_throughput = (result.throughput_std / result.throughput_mean) * 100.0
    assert cv_throughput < 15.0, f"Throughput CV {cv_throughput:.1f}% exceeded noise threshold"
    assert result.is_reproducible is True

    # Check persistence
    stored_runs = repo.get_runs_by_experiment(config.experiment_id)
    assert len(stored_runs) == 2
    for r in stored_runs:
        assert r.git_commit != ""
        assert r.split == "train"
        assert r.status == "COMPLETED"
