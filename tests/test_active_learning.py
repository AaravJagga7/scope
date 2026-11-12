"""Tests for BoTorch SLO boundary acquisition and active learning benchmark."""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import torch
from botorch.models import SingleTaskGP
from botorch.models.transforms.outcome import Standardize

from scopex.discovery import (
    ActiveLearningBenchmark,
    SLOBoundaryAcquisition,
    plot_active_learning_efficiency,
    select_next_candidate_bo,
)
from scopex.discovery.selection import StrategyType
from scopex.discovery.surrogate import PROFILE_ENCODER


def _make_mock_df(n: int = 25, seed: int = 1, split: str = "train") -> pd.DataFrame:
    """Create synthetic DataFrame for testing."""
    rng = np.random.default_rng(seed)
    profiles = list(PROFILE_ENCODER.keys())
    rps = rng.uniform(50, 400, n)
    replicas = rng.choice([1, 2, 4, 8], n).astype(float)
    # Target synthetic p95 with clear threshold boundary around 500ms
    latency = 100.0 + (rps * 2.5) / replicas + rng.normal(0, 15, n)
    return pd.DataFrame(
        {
            "split": split,
            "target_rps": rps,
            "checkout_replicas": replicas,
            "db_pool_size": rng.choice([10, 20, 50], n).astype(float),
            "consumers": rng.choice([1, 2, 4], n).astype(float),
            "cache_enabled": rng.integers(0, 2, n).astype(float),
            "workload_profile": rng.choice(profiles, n),
            "p95_latency_ms": latency.clip(10.0),
        }
    )


@pytest.fixture(scope="module")
def mock_pool() -> pd.DataFrame:
    return _make_mock_df(n=30, seed=10, split="train")


@pytest.fixture(scope="module")
def mock_heldout() -> pd.DataFrame:
    return _make_mock_df(n=12, seed=99, split="held_out")


@pytest.fixture(scope="module")
def fitted_botorch_model() -> SingleTaskGP:
    torch.manual_seed(42)
    X = torch.rand(10, 6, dtype=torch.float64)
    Y = torch.randn(10, 1, dtype=torch.float64) * 100 + 500
    model = SingleTaskGP(X, Y, outcome_transform=Standardize(m=1))
    return model


class TestSLOBoundaryAcquisition:
    def test_init_and_attributes(self, fitted_botorch_model: SingleTaskGP) -> None:
        acq = SLOBoundaryAcquisition(
            model=fitted_botorch_model, threshold=500.0, beta=2.0, method="straddle"
        )
        assert acq.threshold == 500.0
        assert acq.beta == 2.0
        assert acq.method == "straddle"

    def test_invalid_method_raises(self, fitted_botorch_model: SingleTaskGP) -> None:
        with pytest.raises(ValueError, match="Unknown method"):
            SLOBoundaryAcquisition(
                model=fitted_botorch_model, threshold=500.0, method="invalid_method"  # type: ignore[arg-type]
            )

    def test_forward_output_shape(self, fitted_botorch_model: SingleTaskGP) -> None:
        acq = SLOBoundaryAcquisition(model=fitted_botorch_model, threshold=500.0)
        X_test = torch.rand(5, 1, 6, dtype=torch.float64)
        vals = acq(X_test)
        assert vals.shape == torch.Size([5])
        assert torch.all(torch.isfinite(vals))

    def test_weighted_uncertainty_method(self, fitted_botorch_model: SingleTaskGP) -> None:
        acq = SLOBoundaryAcquisition(
            model=fitted_botorch_model, threshold=500.0, method="weighted_uncertainty"
        )
        X_test = torch.rand(4, 1, 6, dtype=torch.float64)
        vals = acq(X_test)
        assert vals.shape == torch.Size([4])
        assert torch.all(vals >= 0.0)

    def test_select_next_candidate_bo(self, fitted_botorch_model: SingleTaskGP) -> None:
        X_pool = torch.rand(15, 6, dtype=torch.float64)
        remaining = [2, 5, 8, 11]
        chosen = select_next_candidate_bo(
            model=fitted_botorch_model,
            X_pool=X_pool,
            remaining_indices=remaining,
            threshold=500.0,
        )
        assert chosen in remaining

    def test_select_next_candidate_empty_raises(self, fitted_botorch_model: SingleTaskGP) -> None:
        X_pool = torch.rand(10, 6, dtype=torch.float64)
        with pytest.raises(ValueError, match="cannot be empty"):
            select_next_candidate_bo(
                model=fitted_botorch_model, X_pool=X_pool, remaining_indices=[]
            )


class TestActiveLearningBenchmark:
    def test_benchmark_run_strategy(
        self, mock_pool: pd.DataFrame, mock_heldout: pd.DataFrame
    ) -> None:
        bench = ActiveLearningBenchmark(
            df_pool=mock_pool,
            df_heldout=mock_heldout,
            threshold=500.0,
            target_accuracy=0.75,
            n_init=4,
            n_steps=4,
            seed=42,
        )
        res_bo = bench.run_strategy(StrategyType.BO, init_indices=[0, 1, 2, 3])
        assert res_bo.strategy == "bo"
        assert len(res_bo.history) == 4
        assert 0.0 <= res_bo.final_accuracy <= 1.0
        assert res_bo.final_rmse >= 0.0

        res_random = bench.run_strategy("random", init_indices=[0, 1, 2, 3])
        assert res_random.strategy == "random"
        assert len(res_random.history) == 4

        res_grid = bench.run_strategy("grid", init_indices=[0, 1, 2, 3])
        assert res_grid.strategy == "grid"
        assert len(res_grid.history) == 4

    def test_benchmark_run_all(
        self, mock_pool: pd.DataFrame, mock_heldout: pd.DataFrame
    ) -> None:
        bench = ActiveLearningBenchmark(
            df_pool=mock_pool,
            df_heldout=mock_heldout,
            threshold=500.0,
            target_accuracy=0.70,
            n_init=4,
            n_steps=3,
            seed=42,
        )
        summary = bench.run_all()
        assert set(summary.results.keys()) == {"bo", "random", "grid"}
        for strat in ("bo", "random", "grid"):
            assert summary.results[strat].final_accuracy >= 0.0
            assert len(summary.results[strat].history) == 3

    def test_invalid_strategy_raises(
        self, mock_pool: pd.DataFrame, mock_heldout: pd.DataFrame
    ) -> None:
        bench = ActiveLearningBenchmark(df_pool=mock_pool, df_heldout=mock_heldout)
        with pytest.raises(ValueError, match="Unknown strategy"):
            bench.run_strategy("unsupported_strategy", init_indices=[0, 1])


class TestPlotActiveLearningEfficiency:
    def test_plot_generation(
        self, mock_pool: pd.DataFrame, mock_heldout: pd.DataFrame
    ) -> None:
        bench = ActiveLearningBenchmark(
            df_pool=mock_pool,
            df_heldout=mock_heldout,
            threshold=500.0,
            target_accuracy=0.75,
            n_init=4,
            n_steps=3,
            seed=42,
        )
        summary = bench.run_all()
        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = Path(tmpdir) / "efficiency.png"
            res_path = plot_active_learning_efficiency(
                results=summary.results,
                save_path=out_path,
                target_accuracy=0.75,
                threshold=500.0,
            )
            assert res_path.exists()
            assert res_path.stat().st_size > 1024

