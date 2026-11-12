"""Unit and integration tests for experiment groups, LHS dataset generation, and CLI."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pandas as pd

from scopex.cli import main
from scopex.storage.dataset import (
    generate_and_store_dataset,
    generate_lhs_configs,
    simulate_system_metrics,
)
from scopex.storage.groups import list_groups, load_group
from scopex.storage.repository import ExperimentRepository


def test_all_declarative_groups_exist_and_valid():
    """Validates that groups A, C, E, F, M are discovered, parsed, and contain valid configs."""
    groups = list_groups()
    group_ids = {g.group_id for g in groups}
    expected = {"A", "C", "E", "F", "M"}
    assert expected.issubset(group_ids), f"Missing groups: {expected - group_ids}"

    for g in groups:
        assert len(g.configurations) > 0, f"Group {g.group_id} has no configurations"
        for cfg in g.configurations:
            cfg.validate()
            assert cfg.checkout_replicas >= 1
            assert cfg.db_pool_size >= 2
            assert cfg.workload.rps >= 10


def test_group_e_database_matrix_resolution():
    """Verifies that Group E resolves db_pool_size variations with fixed replicas."""
    group_e = load_group("E")
    assert group_e.group_id == "E"
    assert len(group_e.configurations) == 8

    pool_sizes = [cfg.db_pool_size for cfg in group_e.configurations]
    assert pool_sizes == [5, 10, 20, 30, 50, 75, 100, 150]

    for cfg in group_e.configurations:
        assert cfg.checkout_replicas == 4
        assert cfg.workload.rps == 400
        assert cfg.cache_enabled is False


def test_group_f_caching_matrix_resolution():
    """Verifies that Group F tests both cache on and off across traffic profiles."""
    group_f = load_group("F")
    assert group_f.group_id == "F"
    assert len(group_f.configurations) == 12

    cache_states = {cfg.cache_enabled for cfg in group_f.configurations}
    assert cache_states == {True, False}

    profiles = {cfg.workload.profile for cfg in group_f.configurations}
    assert profiles == {"normal", "viral_product"}


def test_latin_hypercube_sampling_distribution_and_bounds():
    """Verifies that LHS generates diverse samples covering the continuous & categorical space."""
    n_samples = 50
    configs = generate_lhs_configs(n=n_samples, held_out_ratio=0.20, seed=123)
    assert len(configs) == n_samples

    rps_values = [c.workload.rps for c in configs]
    assert min(rps_values) >= 50
    assert max(rps_values) <= 1000

    replicas_values = [c.checkout_replicas for c in configs]
    assert min(replicas_values) >= 1
    assert max(replicas_values) <= 10

    pool_values = [c.db_pool_size for c in configs]
    assert min(pool_values) >= 5
    assert max(pool_values) <= 100

    cache_values = {c.cache_enabled for c in configs}
    assert True in cache_values and False in cache_values


def test_held_out_partitioning_ratio():
    """Verifies that exactly ~20% of configurations are reserved as held-out test split."""
    n = 100
    ratio = 0.20
    configs = generate_lhs_configs(n=n, held_out_ratio=ratio, seed=42)

    held_out = [c for c in configs if c.split == "held_out"]
    train = [c for c in configs if c.split == "train"]

    assert len(held_out) == 20
    assert len(train) == 80
    assert len(held_out) + len(train) == 100


def test_system_telemetry_simulation_mechanics():
    """Verifies that telemetry simulation exhibits realistic queueing and bottleneck shifts."""
    configs = generate_lhs_configs(n=5, seed=99)
    for cfg in configs:
        metrics = simulate_system_metrics(cfg)
        assert metrics["achieved_rps"] > 0
        lats = metrics["client_side_latency_percentiles"]
        assert lats["p50"] > 0
        assert lats["p95"] >= lats["p50"]
        assert lats["p99"] >= lats["p95"]
        assert 0.0 <= metrics["error_rate_percent"] <= 100.0
        assert metrics["db_connections"] <= cfg.db_pool_size
        assert 10.0 <= metrics["cpu_pct"] <= 100.0


def test_acceptance_dataset_generation_at_least_100_runs():
    """ACCEPTANCE TEST: Dataset has >= 100 labelled experiments with train/held-out split."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "test_experiments.db"
        parquet_path = Path(tmp_dir) / "test_experiments.parquet"
        repo = ExperimentRepository(db_path=db_path, parquet_path=parquet_path)

        runs = generate_and_store_dataset(
            n=120,
            method="lhs",
            held_out_ratio=0.20,
            seed=42,
            repository=repo,
        )

        assert len(runs) >= 100, f"Expected >= 100 runs, got {len(runs)}"

        # Validate SQLite persistence
        assert repo.count_runs() == 120
        train_count = repo.count_runs(split="train")
        held_out_count = repo.count_runs(split="held_out")
        assert train_count == 96
        assert held_out_count == 24
        assert train_count + held_out_count == 120

        # Validate Parquet archive
        assert parquet_path.is_file()
        df = pd.read_parquet(parquet_path)
        assert len(df) == 120
        assert "split" in df.columns
        assert set(df["split"].unique()) == {"train", "held_out"}
        assert (df["split"] == "held_out").sum() == 24

        # Validate discovery isolation: train set never contains held_out records
        train_runs = repo.list_runs(split="train")
        assert len(train_runs) == 96
        assert all(r.split == "train" for r in train_runs)


def test_cli_run_group_e():
    """Tests 'scopex run --group E' via CLI entrypoint."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "cli_test.db"
        parquet_path = Path(tmp_dir) / "cli_test.parquet"

        # Dry run
        ret = main(["run", "--group", "E", "--dry-run", "--db-path", str(db_path)])
        assert ret == 0

        # Quick mock execution (override duration/warmup for instant test)
        ret = main(
            [
                "run",
                "--group",
                "E",
                "--duration",
                "1",
                "--warmup",
                "0",
                "--db-path",
                str(db_path),
                "--parquet-path",
                str(parquet_path),
            ]
        )
        assert ret == 0

        repo = ExperimentRepository(db_path=db_path, parquet_path=parquet_path)
        assert repo.count_runs() == 8


def test_cli_generate_dataset():
    """Tests 'scopex generate-dataset --n 105' via CLI entrypoint."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "cli_dataset.db"
        parquet_path = Path(tmp_dir) / "cli_dataset.parquet"

        ret = main(
            [
                "generate-dataset",
                "--n",
                "105",
                "--method",
                "lhs",
                "--held-out-ratio",
                "0.20",
                "--db-path",
                str(db_path),
                "--parquet-path",
                str(parquet_path),
            ]
        )
        assert ret == 0

        repo = ExperimentRepository(db_path=db_path, parquet_path=parquet_path)
        assert repo.count_runs() == 105
        assert repo.count_runs(split="held_out") == 21
        assert repo.count_runs(split="train") == 84
