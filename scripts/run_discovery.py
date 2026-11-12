#!/usr/bin/env python
"""CLI for fitting GP surrogates and reporting held-out metrics.

Usage::

    python scripts/run_discovery.py --target p95_latency_ms
    python scripts/run_discovery.py --target error_rate_pct
    python scripts/run_discovery.py --all
    python scripts/run_discovery.py --all --save-models

The script:
1. Loads the experiment dataset from ``data/experiments.parquet``.
2. Splits into *train* (``split == "train"``) and *held_out*.
3. Trains a :class:`~scopex.discovery.surrogate.GPSurrogate` **only on train**.
4. Evaluates on **held_out only** — no training data leaks into evaluation.
5. Prints R², RMSE, and calibration coverage.
6. Saves two plots per target to ``data/``.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import pandas as pd

# Ensure repo root is importable when run directly
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scopex.discovery import (
    GPSurrogate,
    compute_metrics,
    plot_predicted_vs_actual,
    plot_uncertainty_vs_traffic,
)
from scopex.discovery.surrogate import SUPPORTED_TARGETS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("run_discovery")


# ---------------------------------------------------------------------------
# Main logic
# ---------------------------------------------------------------------------


def run_target(
    target: str,
    df_train: pd.DataFrame,
    df_heldout: pd.DataFrame,
    *,
    n_iter: int = 200,
    lr: float = 0.1,
    data_dir: Path,
    save_model: bool = False,
) -> dict:
    """Train surrogate for *target* and return metrics dict."""
    logger.info("=" * 60)
    logger.info("Target: %s", target)
    logger.info("Train size: %d  |  Held-out size: %d", len(df_train), len(df_heldout))

    # --- Fit (train split only) ---
    sur = GPSurrogate(target=target, n_iter=n_iter, lr=lr)
    sur.fit(df_train)

    # --- Predict on held-out ---
    mean, std = sur.predict(df_heldout)
    y_true = df_heldout[target].to_numpy()

    # --- Metrics ---
    metrics = compute_metrics(y_true, mean, std)

    logger.info("Held-out metrics for %s:", target)
    logger.info("  R²       = %.4f", metrics["r2"])
    logger.info("  RMSE     = %.4f", metrics["rmse"])
    logger.info("  Coverage = %.1f%%  (90%% CI, z=%.3f)", metrics["calibration_coverage"] * 100, metrics["ci_z"])
    logger.info("  N        = %d", metrics["n_samples"])

    # --- Plots ---
    pva_path = data_dir / f"gp_predicted_vs_actual_{target}.png"
    plot_predicted_vs_actual(y_true, mean, std, target_name=target, save_path=pva_path)

    uv_path = data_dir / f"gp_uncertainty_vs_traffic_{target}.png"
    plot_uncertainty_vs_traffic(df_heldout, mean, std, target_name=target, save_path=uv_path)

    logger.info("Plots saved:")
    logger.info("  %s", pva_path)
    logger.info("  %s", uv_path)

    # --- Optionally save model ---
    if save_model:
        model_path = data_dir / f"gp_surrogate_{target}.pt"
        sur.save(model_path)

    return {
        "target": target,
        "metrics": metrics,
        "plots": [str(pva_path), str(uv_path)],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fit GP surrogates on train split and report held-out metrics."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--target",
        choices=SUPPORTED_TARGETS,
        help="Single target to train and evaluate.",
    )
    group.add_argument(
        "--all",
        action="store_true",
        help="Train and evaluate all supported targets.",
    )
    parser.add_argument(
        "--data",
        default="data/experiments.parquet",
        metavar="PATH",
        help="Path to the experiment Parquet file (default: data/experiments.parquet).",
    )
    parser.add_argument(
        "--n-iter",
        type=int,
        default=200,
        metavar="N",
        help="Number of Adam optimisation steps (default: 200).",
    )
    parser.add_argument(
        "--lr",
        type=float,
        default=0.1,
        metavar="LR",
        help="Adam learning rate (default: 0.1).",
    )
    parser.add_argument(
        "--output-dir",
        default="data",
        metavar="DIR",
        help="Directory for plots and optional model files (default: data/).",
    )
    parser.add_argument(
        "--save-models",
        action="store_true",
        help="Persist trained model .pt files to --output-dir.",
    )
    parser.add_argument(
        "--json-summary",
        metavar="FILE",
        help="Write a JSON summary of all results to FILE.",
    )

    args = parser.parse_args(argv)

    # --- Load dataset ---
    data_path = Path(args.data)
    if not data_path.exists():
        logger.error("Dataset not found: %s", data_path)
        return 1

    df = pd.read_parquet(data_path)
    df_train = df[df["split"] == "train"].copy()
    df_heldout = df[df["split"] == "held_out"].copy()

    if df_train.empty:
        logger.error("No training rows found (split == 'train').")
        return 1
    if df_heldout.empty:
        logger.error("No held-out rows found (split == 'held_out').")
        return 1

    logger.info(
        "Dataset loaded: %d total rows  (%d train, %d held-out)",
        len(df),
        len(df_train),
        len(df_heldout),
    )

    targets = SUPPORTED_TARGETS if args.all else [args.target]
    data_dir = Path(args.output_dir)
    data_dir.mkdir(parents=True, exist_ok=True)

    all_results: list[dict] = []
    for target in targets:
        result = run_target(
            target,
            df_train,
            df_heldout,
            n_iter=args.n_iter,
            lr=args.lr,
            data_dir=data_dir,
            save_model=args.save_models,
        )
        all_results.append(result)

    # --- Summary table ---
    logger.info("")
    logger.info("=" * 60)
    logger.info("SUMMARY (held-out metrics only)")
    logger.info("%-30s  %8s  %10s  %12s", "Target", "R²", "RMSE", "Coverage")
    logger.info("-" * 65)
    for r in all_results:
        m = r["metrics"]
        logger.info(
            "%-30s  %8.4f  %10.2f  %10.1f%%",
            r["target"],
            m["r2"],
            m["rmse"],
            m["calibration_coverage"] * 100,
        )
    logger.info("=" * 60)

    if args.json_summary:
        summary_path = Path(args.json_summary)
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        with open(summary_path, "w") as f:
            json.dump(all_results, f, indent=2, default=str)
        logger.info("JSON summary written to %s", summary_path)

    return 0


if __name__ == "__main__":
    sys.exit(main())
