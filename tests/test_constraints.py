"""Unit and integration tests for interpretable constraint learning with Z3 expressions."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
import z3

from scopex.discovery import (
    Constraint,
    evaluate_and_rank_constraints,
    format_constraints_report,
    learn_constraints,
    learn_decision_tree_constraints,
    learn_sparse_regression_constraints,
)
from scopex.discovery.surrogate import PROFILE_ENCODER


def _make_mock_df(n: int = 40, seed: int = 1, split: str = "train") -> pd.DataFrame:
    """Create synthetic DataFrame for testing."""
    rng = np.random.default_rng(seed)
    profiles = list(PROFILE_ENCODER.keys())
    rps = rng.uniform(50, 800, n)
    replicas = rng.choice([1, 2, 4, 8], n).astype(float)
    cache = rng.integers(0, 2, n).astype(float)

    # Synthetic latency: high RPS + no cache -> high latency violation
    latency = 100.0 + (rps * 1.5) / (replicas + 0.1) - (cache * 200.0) + rng.normal(0, 15, n)
    return pd.DataFrame(
        {
            "run_id": [f"run_{split}_{i}" for i in range(n)],
            "split": split,
            "target_rps": rps,
            "checkout_replicas": replicas,
            "db_pool_size": rng.choice([10, 20, 50], n).astype(float),
            "consumers": rng.choice([1, 2, 4], n).astype(float),
            "cache_enabled": cache,
            "workload_profile": rng.choice(profiles, n),
            "p95_latency_ms": latency.clip(10.0),
        }
    )


@pytest.fixture(scope="module")
def df_train() -> pd.DataFrame:
    return _make_mock_df(n=50, seed=42, split="train")


@pytest.fixture(scope="module")
def df_heldout() -> pd.DataFrame:
    return _make_mock_df(n=20, seed=123, split="held_out")


class TestConstraintObject:
    def test_constraint_creation_and_attributes(self) -> None:
        c = Constraint(
            variables=["target_rps", "checkout_replicas"],
            inequality="target_rps > 400.0 and checkout_replicas <= 2.0",
            confidence=0.90,
            support=0.25,
            support_count=10,
            source_experiments=["run_train_1", "run_train_2"],
            z3_expr="And(target_rps > 400.0, checkout_replicas <= 2.0)",
            method="decision_tree",
        )
        assert c.variables == ["target_rps", "checkout_replicas"]
        assert c.confidence == 0.90
        assert c.support == 0.25
        assert c.support_count == 10
        assert c.method == "decision_tree"
        assert "IF" in c.human_readable()

    def test_constraint_to_z3_ast(self) -> None:
        c = Constraint(
            variables=["target_rps", "cache_enabled"],
            inequality="target_rps > 500.0 and cache_enabled <= 0.0",
            confidence=0.85,
            support=0.30,
            support_count=15,
            source_experiments=[],
            z3_expr="And(target_rps > 500.0, cache_enabled <= 0.0)",
            method="decision_tree",
        )
        z3_ast = c.to_z3()
        assert isinstance(z3_ast, z3.BoolRef)
        smt2_str = c.to_smt2()
        assert "(and" in smt2_str
        assert "target_rps" in smt2_str

    def test_constraint_matches_dataframe(self) -> None:
        df = pd.DataFrame(
            {
                "target_rps": [300.0, 600.0, 700.0],
                "cache_enabled": [0.0, 0.0, 1.0],
            }
        )
        c = Constraint(
            variables=["target_rps", "cache_enabled"],
            inequality="target_rps > 500.0 and cache_enabled <= 0.0",
            confidence=1.0,
            support=0.33,
            support_count=1,
            source_experiments=[],
            z3_expr="And(target_rps > 500.0, cache_enabled <= 0.0)",
            method="decision_tree",
            evaluator=lambda d: (d["target_rps"] > 500.0) & (d["cache_enabled"] <= 0.0),
        )
        mask = c.matches(df)
        assert np.array_equal(mask, [False, True, False])

    def test_to_dict_serialization(self) -> None:
        c = Constraint(
            variables=["target_rps"],
            inequality="target_rps > 300.0",
            confidence=0.8,
            support=0.4,
            support_count=8,
            source_experiments=["run_1"],
            z3_expr="target_rps > 300.0",
            method="decision_tree",
            heldout_precision=0.85,
            heldout_recall=0.70,
            heldout_f1=0.77,
            heldout_support=12,
        )
        d = c.to_dict()
        assert d["variables"] == ["target_rps"]
        assert d["heldout_precision"] == 0.85
        assert d["heldout_recall"] == 0.70
        assert "smt2" in d


class TestDecisionTreeConstraints:
    def test_learn_dt_constraints(self, df_train: pd.DataFrame) -> None:
        rules = learn_decision_tree_constraints(
            df_train=df_train,
            target_col="p95_latency_ms",
            threshold=400.0,
            max_depths=[2, 3],
            min_samples_leaf=4,
            min_confidence=0.60,
        )
        assert len(rules) > 0
        for r in rules:
            assert r.method == "decision_tree"
            assert r.confidence >= 0.60
            assert len(r.variables) >= 1
            assert len(r.source_experiments) >= 4
            # Z3 AST validity
            z3_ast = r.to_z3()
            assert isinstance(z3_ast, z3.BoolRef)


class TestSparseRegressionConstraints:
    def test_learn_sparse_regression_constraints(self, df_train: pd.DataFrame) -> None:
        rules = learn_sparse_regression_constraints(
            df_train=df_train,
            target_col="p95_latency_ms",
            threshold=400.0,
            alphas=[0.01, 0.03, 0.08],
            min_confidence=0.50,
        )
        assert len(rules) > 0
        for r in rules:
            assert r.method == "sparse_regression"
            assert r.confidence >= 0.50
            assert len(r.variables) >= 1
            z3_ast = r.to_z3()
            assert isinstance(z3_ast, z3.BoolRef)


class TestEvaluateAndRankConstraints:
    def test_ranking_and_metrics_population(
        self, df_train: pd.DataFrame, df_heldout: pd.DataFrame
    ) -> None:
        candidates = learn_constraints(
            df_train=df_train, target_col="p95_latency_ms", threshold=400.0
        )
        assert len(candidates) > 0

        ranked = evaluate_and_rank_constraints(
            constraints=candidates,
            df_heldout=df_heldout,
            target_col="p95_latency_ms",
            threshold=400.0,
        )
        assert len(ranked) == len(candidates)

        # Check metrics populated
        for c in ranked:
            assert c.heldout_precision is not None
            assert 0.0 <= c.heldout_precision <= 1.0
            assert c.heldout_recall is not None
            assert 0.0 <= c.heldout_recall <= 1.0
            assert c.heldout_f1 is not None

        # Check sorted order (precision non-increasing)
        precisions = [c.heldout_precision for c in ranked if c.heldout_precision is not None]
        assert precisions == sorted(precisions, reverse=True)

    def test_format_constraints_report(
        self, df_train: pd.DataFrame, df_heldout: pd.DataFrame
    ) -> None:
        candidates = learn_constraints(
            df_train=df_train, target_col="p95_latency_ms", threshold=400.0
        )
        ranked = evaluate_and_rank_constraints(
            constraints=candidates,
            df_heldout=df_heldout,
            target_col="p95_latency_ms",
            threshold=400.0,
        )
        report = format_constraints_report(ranked, top_n=3)
        assert "TOP OPERATIONAL CONSTRAINTS" in report
        assert "Rank" in report
        assert "Held-Out P" in report
        assert "Rule:" in report
        assert "Z3 Expr:" in report

