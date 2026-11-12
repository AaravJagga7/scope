"""Unit and integration tests for cross-scale constraint evolution."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from scopex.discovery import (
    ComparisonRow,
    ConstraintStatus,
    CrossScaleEvolutionEngine,
    format_evolution_report,
    select_verification_experiments_gp,
)


def _make_mock_scale_df(n: int = 30, replicas: int = 2, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rps = rng.uniform(50, 600, n)
    cache = rng.integers(0, 2, n).astype(float)
    pool = rng.choice([10, 20, 50], n).astype(float)
    latency = 80.0 + (rps * 2.0) / float(replicas) - (cache * 150.0) + rng.normal(0, 10, n)
    return pd.DataFrame(
        {
            "run_id": [f"run_{replicas}rep_{i}" for i in range(n)],
            "split": "train",
            "target_rps": rps,
            "checkout_replicas": float(replicas),
            "db_pool_size": pool,
            "consumers": 2.0,
            "cache_enabled": cache,
            "workload_profile": "normal",
            "p95_latency_ms": latency.clip(10.0),
            "error_rate_pct": np.where(latency > 450.0, 5.0, 0.0),
        }
    )


@pytest.fixture(scope="module")
def df_source() -> pd.DataFrame:
    return _make_mock_scale_df(n=25, replicas=2, seed=1)


@pytest.fixture(scope="module")
def df_target_pool() -> pd.DataFrame:
    return _make_mock_scale_df(n=25, replicas=4, seed=2)


@pytest.fixture(scope="module")
def df_test() -> pd.DataFrame:
    return _make_mock_scale_df(n=15, replicas=4, seed=99)


class TestConstraintStatusEnum:
    def test_enum_values(self) -> None:
        assert ConstraintStatus.UNCHANGED == "unchanged"
        assert ConstraintStatus.RELAXED == "relaxed"
        assert ConstraintStatus.TIGHTENED == "tightened"
        assert ConstraintStatus.INVALIDATED == "invalidated"
        assert ConstraintStatus.NEW == "new"


class TestSelectVerificationExperimentsGP:
    def test_selection_returns_requested_count(
        self, df_source: pd.DataFrame, df_target_pool: pd.DataFrame
    ) -> None:
        selected = select_verification_experiments_gp(
            df_pool=df_target_pool,
            df_source=df_source,
            n_verify=4,
        )
        assert len(selected) == 4
        assert "target_rps" in selected.columns


class TestCrossScaleEvolutionEngine:
    def test_predict_shifts_and_classification(
        self, df_source: pd.DataFrame, df_target_pool: pd.DataFrame, df_test: pd.DataFrame
    ) -> None:
        engine = CrossScaleEvolutionEngine(source_replicas=2, target_replicas=4)
        assert engine.scale_factor == 2.0

        source_constraints = engine.retrieve_source_constraints(df_source)
        shifts = engine.predict_shifts(source_constraints)
        assert len(shifts) == len(source_constraints)

        evolved = engine.evolve_and_classify(
            source_constraints=source_constraints,
            df_verification=df_target_pool.head(5),
            df_target_pool=df_target_pool,
        )

        # Check all 5 required classifications are represented
        statuses = {ec.status for ec in evolved}
        assert ConstraintStatus.UNCHANGED in statuses
        assert ConstraintStatus.RELAXED in statuses
        assert ConstraintStatus.TIGHTENED in statuses
        assert ConstraintStatus.INVALIDATED in statuses
        assert ConstraintStatus.NEW in statuses

    def test_benchmark_comparison_table(
        self, df_source: pd.DataFrame, df_target_pool: pd.DataFrame, df_test: pd.DataFrame
    ) -> None:
        engine = CrossScaleEvolutionEngine(source_replicas=2, target_replicas=4)
        result = engine.run_benchmark_comparison(
            df_source=df_source,
            df_target_pool=df_target_pool,
            df_test=df_test,
            budgets=[2, 4, 8],
        )

        assert len(result.comparison_table) == 3
        first_row = result.comparison_table[0]
        assert isinstance(first_row, ComparisonRow)
        assert first_row.experiments == 2
        # Incremental evolution should demonstrate sample efficiency advantage at low budget
        assert first_row.incremental_accuracy >= first_row.full_rediscovery_accuracy

        # Report formatting
        report = format_evolution_report(result)
        assert "CROSS-SCALE CONSTRAINT EVOLUTION" in report
        assert "SAMPLE EFFICIENCY BENCHMARK" in report
        assert "Incremental Acc" in report
