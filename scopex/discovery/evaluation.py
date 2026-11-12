"""Evaluation utilities for the GP surrogate: metrics and plots.

All metrics are computed on the **held-out** split only. Training data must
never be passed to these functions when reporting final numbers.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")  # headless rendering — must be set before pyplot import
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.metrics import r2_score

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def compute_metrics(
    y_true: np.ndarray,
    y_mean: np.ndarray,
    y_std: np.ndarray,
    ci_z: float = 1.645,
) -> dict[str, float]:
    """Compute R², RMSE and calibration coverage on a held-out set.

    Calibration is the empirical fraction of held-out points whose true value
    falls inside the ``ci_z``-σ credible interval (default 90 % CI → z = 1.645).
    A perfectly calibrated GP should achieve coverage ≈ 90 %.

    Args:
        y_true: Ground-truth targets, shape (n,).
        y_mean: Posterior predictive mean, shape (n,).
        y_std:  Posterior predictive std, shape (n,).
        ci_z:   Number of standard deviations for the credible interval.

    Returns:
        Dictionary with keys ``r2``, ``rmse``, ``calibration_coverage``,
        ``ci_z``, ``n_samples``.
    """
    y_true = np.asarray(y_true, dtype=float)
    y_mean = np.asarray(y_mean, dtype=float)
    y_std = np.asarray(y_std, dtype=float)

    n = len(y_true)
    rmse = float(np.sqrt(np.mean((y_true - y_mean) ** 2)))
    r2 = float(r2_score(y_true, y_mean))

    lower = y_mean - ci_z * y_std
    upper = y_mean + ci_z * y_std
    coverage = float(np.mean((y_true >= lower) & (y_true <= upper)))

    return {
        "r2": r2,
        "rmse": rmse,
        "calibration_coverage": coverage,
        "ci_z": ci_z,
        "n_samples": n,
    }


# ---------------------------------------------------------------------------
# Plots
# ---------------------------------------------------------------------------


def plot_predicted_vs_actual(
    y_true: np.ndarray,
    y_mean: np.ndarray,
    y_std: np.ndarray,
    target_name: str,
    save_path: str | Path,
    *,
    ci_z: float = 2.0,
) -> Path:
    """Scatter plot of predicted vs. actual values with ±``ci_z``σ error bars.

    A perfect predictor lies on the diagonal dashed line. R² and RMSE are
    shown in the legend. Error bars reflect the GP's predictive uncertainty.

    Args:
        y_true: Ground-truth values.
        y_mean: Posterior mean predictions.
        y_std:  Posterior standard deviations.
        target_name: Human-readable target label (used in axis titles).
        save_path: Output PNG file path.
        ci_z: Error bar half-width in units of σ.

    Returns:
        Resolved absolute :class:`~pathlib.Path` of the saved figure.
    """
    y_true = np.asarray(y_true, dtype=float)
    y_mean = np.asarray(y_mean, dtype=float)
    y_std = np.asarray(y_std, dtype=float)

    metrics = compute_metrics(y_true, y_mean, y_std)
    r2 = metrics["r2"]
    rmse = metrics["rmse"]

    fig, ax = plt.subplots(figsize=(7, 6))

    ax.errorbar(
        y_true,
        y_mean,
        yerr=ci_z * y_std,
        fmt="o",
        alpha=0.55,
        markersize=5,
        linewidth=0.8,
        capsize=3,
        label=f"Predictions  R²={r2:.3f}  RMSE={rmse:.1f}",
        color="steelblue",
        ecolor="steelblue",
    )

    all_vals = np.concatenate([y_true, y_mean])
    lo, hi = all_vals.min(), all_vals.max()
    margin = (hi - lo) * 0.05
    diag = np.array([lo - margin, hi + margin])
    ax.plot(diag, diag, "k--", linewidth=1, label="Perfect prediction")

    ax.set_xlabel(f"Actual {target_name}", fontsize=12)
    ax.set_ylabel(f"Predicted {target_name}", fontsize=12)
    ax.set_title(f"GP Surrogate — Predicted vs Actual\n{target_name}", fontsize=13)
    ax.legend(fontsize=10)
    ax.grid(True, alpha=0.3)

    save_path = Path(save_path).resolve()
    save_path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(save_path, dpi=150)
    plt.close(fig)
    logger.info("Saved predicted-vs-actual plot to %s", save_path)
    return save_path


def plot_uncertainty_vs_traffic(
    df: pd.DataFrame,
    y_mean: np.ndarray,
    y_std: np.ndarray,
    target_name: str,
    save_path: str | Path,
    *,
    ci_z: float = 2.0,
    target_col: str | None = None,
) -> Path:
    """Ribbon plot of predicted mean ± ``ci_z``σ as a function of ``target_rps``.

    Points are sorted by ``target_rps``. Actual observations (if present) are
    overlaid as scatter points. The ribbon visualises how predictive uncertainty
    varies across traffic levels.

    Args:
        df: DataFrame aligned with *y_mean* / *y_std* (must have ``target_rps``).
        y_mean: Posterior mean predictions, shape (n,).
        y_std:  Posterior standard deviations, shape (n,).
        target_name: Human-readable target label.
        save_path: Output PNG file path.
        ci_z: Ribbon half-width in units of σ.
        target_col: Column name in *df* for actual values. If *None*, inferred
            from *target_name*.

    Returns:
        Resolved absolute :class:`~pathlib.Path` of the saved figure.
    """
    y_mean = np.asarray(y_mean, dtype=float)
    y_std = np.asarray(y_std, dtype=float)

    rps = df["target_rps"].to_numpy(dtype=float)
    sort_idx = np.argsort(rps)
    rps_s = rps[sort_idx]
    mean_s = y_mean[sort_idx]
    std_s = y_std[sort_idx]

    fig, ax = plt.subplots(figsize=(9, 5))

    ax.fill_between(
        rps_s,
        mean_s - ci_z * std_s,
        mean_s + ci_z * std_s,
        alpha=0.25,
        color="steelblue",
        label=f"±{ci_z}σ uncertainty",
    )
    ax.plot(rps_s, mean_s, color="steelblue", linewidth=1.8, label="GP mean")

    # Overlay actual observations when available
    actual_col = target_col or target_name
    if actual_col in df.columns:
        y_actual = df[actual_col].to_numpy(dtype=float)[sort_idx]
        ax.scatter(
            rps_s,
            y_actual,
            s=18,
            color="tomato",
            alpha=0.7,
            zorder=3,
            label="Actual (held-out)",
        )

    ax.set_xlabel("Target RPS", fontsize=12)
    ax.set_ylabel(target_name, fontsize=12)
    ax.set_title(f"GP Predictive Uncertainty vs Traffic Level\n{target_name}", fontsize=13)
    ax.legend(fontsize=10)
    ax.grid(True, alpha=0.3)

    save_path = Path(save_path).resolve()
    save_path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(save_path, dpi=150)
    plt.close(fig)
    logger.info("Saved uncertainty-vs-traffic plot to %s", save_path)
    return save_path


def plot_active_learning_efficiency(
    results: dict[str, Any],
    save_path: str | Path,
    target_accuracy: float = 0.80,
    threshold: float = 500.0,
) -> Path:
    """Plot learning curves comparing BO (SLO boundary), Random Search, and Grid Search.

    Produces a dual-panel figure:
    - Left panel: Held-out SLO boundary classification accuracy (%) vs. number of experiments,
      with the target accuracy threshold line and milestone markers.
    - Right panel: Held-out Latency RMSE (ms) vs. number of experiments.

    Args:
        results: Dictionary mapping strategy name ('bo', 'random', 'grid') to
            StrategyResult or dict containing history records.
        save_path: Destination PNG file path.
        target_accuracy: Accuracy threshold to highlight (e.g. 0.80 for 80%).
        threshold: SLO boundary value (e.g. 500.0 ms).

    Returns:
        Resolved absolute Path to the saved figure.
    """
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5.5))

    styles = {
        "bo": {
            "label": "Bayesian Optimization (SLO Boundary)",
            "color": "#1f77b4",
            "marker": "o",
            "linewidth": 2.2,
        },
        "random": {
            "label": "Random Search",
            "color": "#ff7f0e",
            "marker": "s",
            "linewidth": 1.8,
        },
        "grid": {
            "label": "Grid Search",
            "color": "#2ca02c",
            "marker": "^",
            "linewidth": 1.8,
        },
    }

    milestones: dict[str, int | None] = {}

    for strat_key, style in styles.items():
        if strat_key not in results:
            continue
        res = results[strat_key]
        history = res.history if hasattr(res, "history") else res.get("history", [])
        milestones[strat_key] = (
            res.experiments_to_target
            if hasattr(res, "experiments_to_target")
            else res.get("experiments_to_target")
        )

        n_exps = [h.n_experiments if hasattr(h, "n_experiments") else h["n_experiments"] for h in history]
        accs = [
            (h.accuracy if hasattr(h, "accuracy") else h["accuracy"]) * 100.0
            for h in history
        ]
        rmses = [h.rmse if hasattr(h, "rmse") else h["rmse"] for h in history]

        # Left: Accuracy
        ax1.plot(
            n_exps,
            accs,
            label=style["label"],
            color=style["color"],
            marker=style["marker"],
            markersize=4,
            linewidth=style["linewidth"],
            alpha=0.9,
        )

        # Right: RMSE
        ax2.plot(
            n_exps,
            rmses,
            label=style["label"],
            color=style["color"],
            marker=style["marker"],
            markersize=4,
            linewidth=style["linewidth"],
            alpha=0.9,
        )

    # Reference target accuracy line
    ax1.axhline(
        target_accuracy * 100.0,
        color="crimson",
        linestyle="--",
        linewidth=1.4,
        label=f"Target Accuracy ({int(target_accuracy * 100)}%)",
    )

    # Highlight milestones on accuracy plot
    for strat_key, m_exp in milestones.items():
        if m_exp is not None and strat_key in styles:
            color = styles[strat_key]["color"]
            ax1.axvline(
                m_exp,
                color=color,
                linestyle=":",
                linewidth=1.2,
                alpha=0.7,
            )
            ax1.scatter(
                [m_exp],
                [target_accuracy * 100.0],
                color=color,
                s=80,
                zorder=5,
                edgecolor="black",
            )

    ax1.set_xlabel("Number of Experiments", fontsize=11)
    ax1.set_ylabel("Held-Out SLO Accuracy (%)", fontsize=11)
    ax1.set_title(
        f"Sample Efficiency: Accuracy on SLO Boundary (p95 ≤ {threshold:.0f}ms)",
        fontsize=12,
        fontweight="bold",
    )
    ax1.set_ylim(40, 100)
    ax1.grid(True, alpha=0.3)
    ax1.legend(loc="lower right", fontsize=9)

    ax2.set_xlabel("Number of Experiments", fontsize=11)
    ax2.set_ylabel("Held-Out RMSE (ms)", fontsize=11)
    ax2.set_title(
        "Predictive Error Convergence (RMSE)",
        fontsize=12,
        fontweight="bold",
    )
    ax2.grid(True, alpha=0.3)
    ax2.legend(loc="upper right", fontsize=9)

    save_path = Path(save_path).resolve()
    save_path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(save_path, dpi=150)
    plt.close(fig)
    logger.info("Saved active learning efficiency plot to %s", save_path)
    return save_path

