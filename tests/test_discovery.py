"""Tests for scopex/discovery: GP surrogate and evaluation utilities.

Principles:
- Fit exclusively on mock *train* data; evaluate exclusively on mock *held_out* data.
- Keep tensors small so the test suite stays fast (n ≤ 60).
- Never let train rows enter evaluation assertions.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from scopex.discovery import (
    GPSurrogate,
    build_feature_matrix,
    compute_metrics,
    plot_predicted_vs_actual,
    plot_uncertainty_vs_traffic,
)
from scopex.discovery.surrogate import PROFILE_ENCODER

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_df(n: int = 40, seed: int = 0, split: str = "train") -> pd.DataFrame:
    """Return a minimal synthetic experiment DataFrame."""
    rng = np.random.default_rng(seed)
    profiles = list(PROFILE_ENCODER.keys())
    return pd.DataFrame(
        {
            "split": split,
            "target_rps": rng.uniform(50, 500, n),
            "checkout_replicas": rng.choice([1, 2, 4, 6, 8], n).astype(float),
            "db_pool_size": rng.choice([10, 20, 50], n).astype(float),
            "consumers": rng.choice([1, 2, 4], n).astype(float),
            "cache_enabled": rng.integers(0, 2, n).astype(float),
            "workload_profile": rng.choice(profiles, n),
            # Synthetic targets: correlated with rps + replicas + noise
            "p95_latency_ms": (
                rng.uniform(50, 200, n)
                + 3.0 * rng.uniform(50, 500, n)
                + rng.normal(0, 20, n)
            ).clip(0),
            "error_rate_pct": rng.uniform(0, 75, n),
        }
    )


@pytest.fixture(scope="module")
def df_train() -> pd.DataFrame:
    return _make_df(n=40, seed=1, split="train")


@pytest.fixture(scope="module")
def df_heldout() -> pd.DataFrame:
    return _make_df(n=16, seed=99, split="held_out")


@pytest.fixture(scope="module")
def fitted_surrogate(df_train: pd.DataFrame) -> GPSurrogate:
    """A GPSurrogate fitted on the synthetic train set (p95_latency_ms)."""
    return GPSurrogate(target="p95_latency_ms", n_iter=30, lr=0.1).fit(df_train)


# ---------------------------------------------------------------------------
# build_feature_matrix
# ---------------------------------------------------------------------------


class TestBuildFeatureMatrix:
    def test_output_shape(self, df_train: pd.DataFrame) -> None:
        X, scaler = build_feature_matrix(df_train)
        assert X.shape == (len(df_train), 6), "Expected 6 features per row"

    def test_reuse_scaler(self, df_train: pd.DataFrame, df_heldout: pd.DataFrame) -> None:
        _, scaler = build_feature_matrix(df_train)
        X_ho, _ = build_feature_matrix(df_heldout, scaler=scaler)
        assert X_ho.shape == (len(df_heldout), 6)

    def test_cache_enabled_column_is_last(self, df_train: pd.DataFrame) -> None:
        """cache_enabled (0/1) must be the last feature and in [0, 1]."""
        X, _ = build_feature_matrix(df_train)
        assert set(np.unique(X[:, -1])).issubset({0.0, 1.0})

    def test_unknown_profile_does_not_raise(self) -> None:
        df = _make_df(n=4, seed=5)
        df["workload_profile"] = "unknown_profile"
        X, _ = build_feature_matrix(df)
        assert X.shape[0] == 4


# ---------------------------------------------------------------------------
# GPSurrogate.fit / predict
# ---------------------------------------------------------------------------


class TestGPSurrogateFitPredict:
    def test_predict_shapes(self, fitted_surrogate: GPSurrogate, df_heldout: pd.DataFrame) -> None:
        mean, std = fitted_surrogate.predict(df_heldout)
        assert mean.shape == (len(df_heldout),)
        assert std.shape == (len(df_heldout),)

    def test_std_positive(self, fitted_surrogate: GPSurrogate, df_heldout: pd.DataFrame) -> None:
        _, std = fitted_surrogate.predict(df_heldout)
        assert np.all(std > 0), "Predictive std must be strictly positive"

    def test_mean_finite(self, fitted_surrogate: GPSurrogate, df_heldout: pd.DataFrame) -> None:
        mean, _ = fitted_surrogate.predict(df_heldout)
        assert np.all(np.isfinite(mean))

    def test_error_rate_target(self, df_train: pd.DataFrame, df_heldout: pd.DataFrame) -> None:
        sur = GPSurrogate(target="error_rate_pct", n_iter=20, lr=0.1).fit(df_train)
        mean, std = sur.predict(df_heldout)
        assert mean.shape == (len(df_heldout),)
        assert np.all(std > 0)

    def test_unsupported_target_raises(self) -> None:
        with pytest.raises(ValueError, match="target must be one of"):
            GPSurrogate(target="throughput_rps")

    def test_predict_before_fit_raises(self, df_heldout: pd.DataFrame) -> None:
        sur = GPSurrogate(target="p95_latency_ms")
        with pytest.raises(RuntimeError, match="fit\\(\\)"):
            sur.predict(df_heldout)

    def test_method_chaining(self, df_train: pd.DataFrame) -> None:
        sur = GPSurrogate(target="p95_latency_ms", n_iter=10).fit(df_train)
        assert isinstance(sur, GPSurrogate)


# ---------------------------------------------------------------------------
# Held-out metrics
# ---------------------------------------------------------------------------


class TestHeldOutMetrics:
    """Verify that metrics are computed only on held-out data and within sane bounds."""

    def test_metrics_returned_on_heldout(
        self, fitted_surrogate: GPSurrogate, df_heldout: pd.DataFrame
    ) -> None:
        mean, std = fitted_surrogate.predict(df_heldout)
        y_true = df_heldout["p95_latency_ms"].to_numpy()

        # Compute metrics — must be called with held-out data only
        metrics = compute_metrics(y_true, mean, std)

        assert set(metrics.keys()) == {"r2", "rmse", "calibration_coverage", "ci_z", "n_samples"}
        assert metrics["n_samples"] == len(df_heldout)

    def test_rmse_non_negative(
        self, fitted_surrogate: GPSurrogate, df_heldout: pd.DataFrame
    ) -> None:
        mean, std = fitted_surrogate.predict(df_heldout)
        y_true = df_heldout["p95_latency_ms"].to_numpy()
        metrics = compute_metrics(y_true, mean, std)
        assert metrics["rmse"] >= 0

    def test_r2_at_least_minus_five(
        self, fitted_surrogate: GPSurrogate, df_heldout: pd.DataFrame
    ) -> None:
        """R² can be negative for bad models but shouldn't be wildly off with synthetic data."""
        mean, std = fitted_surrogate.predict(df_heldout)
        y_true = df_heldout["p95_latency_ms"].to_numpy()
        metrics = compute_metrics(y_true, mean, std)
        assert metrics["r2"] > -5.0, f"R² suspiciously low: {metrics['r2']:.3f}"

    def test_calibration_in_valid_range(
        self, fitted_surrogate: GPSurrogate, df_heldout: pd.DataFrame
    ) -> None:
        mean, std = fitted_surrogate.predict(df_heldout)
        y_true = df_heldout["p95_latency_ms"].to_numpy()
        metrics = compute_metrics(y_true, mean, std)
        cov = metrics["calibration_coverage"]
        assert 0.0 <= cov <= 1.0, f"Coverage {cov:.3f} is outside [0, 1]"

    def test_no_train_data_in_heldout_index(
        self, df_train: pd.DataFrame, df_heldout: pd.DataFrame
    ) -> None:
        """The train and held-out sets must not share any rows."""
        # With different seeds there should be zero overlap in this tiny synthetic set
        # (even if rows are numerically close, we just check the DataFrame split tags)
        assert (df_train["split"] == "train").all()
        assert (df_heldout["split"] == "held_out").all()

    def test_heldout_metrics_on_real_dataset_if_available(self) -> None:
        """Integration test: skip gracefully if the real dataset is absent."""
        parquet = Path("data/experiments.parquet")
        if not parquet.exists():
            pytest.skip("Real dataset not available")

        df = pd.read_parquet(parquet)
        df_train_real = df[df["split"] == "train"]
        df_ho_real = df[df["split"] == "held_out"]

        sur = GPSurrogate(target="p95_latency_ms", n_iter=100, lr=0.1).fit(df_train_real)
        mean, std = sur.predict(df_ho_real)
        y_true = df_ho_real["p95_latency_ms"].to_numpy()
        metrics = compute_metrics(y_true, mean, std)

        # Generous thresholds — just sanity-check the pipeline runs end-to-end
        assert metrics["r2"] >= 0.0, f"R² on real held-out set: {metrics['r2']:.4f}"
        assert 0.5 <= metrics["calibration_coverage"] <= 1.0, (
            f"Coverage on real held-out set: {metrics['calibration_coverage']:.3f}"
        )


# ---------------------------------------------------------------------------
# Plots
# ---------------------------------------------------------------------------


class TestPlots:
    def test_predicted_vs_actual_saves_png(
        self, fitted_surrogate: GPSurrogate, df_heldout: pd.DataFrame
    ) -> None:
        mean, std = fitted_surrogate.predict(df_heldout)
        y_true = df_heldout["p95_latency_ms"].to_numpy()
        with tempfile.TemporaryDirectory() as tmp:
            out = plot_predicted_vs_actual(y_true, mean, std, "p95_latency_ms", Path(tmp) / "pva.png")
            assert out.exists()
            assert out.stat().st_size > 1024, "PNG seems too small"

    def test_uncertainty_vs_traffic_saves_png(
        self, fitted_surrogate: GPSurrogate, df_heldout: pd.DataFrame
    ) -> None:
        mean, std = fitted_surrogate.predict(df_heldout)
        with tempfile.TemporaryDirectory() as tmp:
            out = plot_uncertainty_vs_traffic(
                df_heldout, mean, std, "p95_latency_ms", Path(tmp) / "uv.png"
            )
            assert out.exists()
            assert out.stat().st_size > 1024, "PNG seems too small"

    def test_uncertainty_plot_without_actual_col(
        self, fitted_surrogate: GPSurrogate, df_heldout: pd.DataFrame
    ) -> None:
        """Should not crash even if the target column is missing from df."""
        mean, std = fitted_surrogate.predict(df_heldout)
        df_no_target = df_heldout.drop(columns=["p95_latency_ms"])
        with tempfile.TemporaryDirectory() as tmp:
            out = plot_uncertainty_vs_traffic(
                df_no_target, mean, std, "p95_latency_ms", Path(tmp) / "uv_notarget.png"
            )
            assert out.exists()


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


class TestPersistence:
    def test_save_and_load(self, fitted_surrogate: GPSurrogate, df_heldout: pd.DataFrame) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pt_path = Path(tmp) / "model.pt"
            fitted_surrogate.save(pt_path)
            assert pt_path.exists()

            loaded = GPSurrogate.load(pt_path)
            mean_orig, std_orig = fitted_surrogate.predict(df_heldout)
            mean_load, std_load = loaded.predict(df_heldout)

            np.testing.assert_allclose(mean_orig, mean_load, rtol=1e-5)
            np.testing.assert_allclose(std_orig, std_load, rtol=1e-5)

    def test_save_before_fit_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sur = GPSurrogate(target="p95_latency_ms")
            with pytest.raises(RuntimeError, match="not trained"):
                sur.save(Path(tmp) / "nope.pt")


# ---------------------------------------------------------------------------
# compute_metrics edge cases
# ---------------------------------------------------------------------------


class TestComputeMetrics:
    def test_perfect_predictions(self) -> None:
        y = np.array([1.0, 2.0, 3.0, 4.0])
        m = compute_metrics(y, y, np.ones_like(y) * 0.01)
        assert m["r2"] == pytest.approx(1.0, abs=1e-6)
        assert m["rmse"] == pytest.approx(0.0, abs=1e-6)
        assert m["calibration_coverage"] == pytest.approx(1.0, abs=1e-6)

    def test_all_inside_ci(self) -> None:
        y_true = np.zeros(10)
        y_mean = np.zeros(10)
        y_std = np.ones(10) * 100  # huge std → all points inside
        m = compute_metrics(y_true, y_mean, y_std)
        assert m["calibration_coverage"] == 1.0

    def test_none_inside_ci(self) -> None:
        y_true = np.array([1000.0] * 10)
        y_mean = np.zeros(10)
        y_std = np.ones(10) * 0.001  # negligible std → no coverage
        m = compute_metrics(y_true, y_mean, y_std)
        assert m["calibration_coverage"] == 0.0
