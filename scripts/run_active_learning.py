#!/usr/bin/env python
"""CLI to run active learning benchmark: BO vs Random vs Grid for SLO boundary discovery.

Usage::

    python scripts/run_active_learning.py
    python scripts/run_active_learning.py --target-accuracy 0.80 --threshold 500.0 --n-steps 45

Acceptance:
- Saves learning curve figure showing BO vs Random vs Grid efficiency to data/active_learning_efficiency.png.
- Reports number of experiments to reach target held-out accuracy.
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
    ActiveLearningBenchmark,
    plot_active_learning_efficiency,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("run_active_learning")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Benchmark BO (SLO boundary) vs Random vs Grid search efficiency."
    )
    parser.add_argument(
        "--data",
        default="data/experiments.parquet",
        metavar="PATH",
        help="Path to experiments Parquet file (default: data/experiments.parquet).",
    )
    parser.add_argument(
        "--target",
        default="p95_latency_ms",
        help="Target column to model (default: p95_latency_ms).",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=500.0,
        help="SLO latency threshold in ms (default: 500.0).",
    )
    parser.add_argument(
        "--target-accuracy",
        type=float,
        default=0.80,
        help="Milestone accuracy threshold on held-out set (default: 0.80).",
    )
    parser.add_argument(
        "--n-init",
        type=int,
        default=8,
        help="Number of initial random seeds (default: 8).",
    )
    parser.add_argument(
        "--n-steps",
        type=int,
        default=45,
        help="Number of sequential selection steps (default: 45).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for reproducibility (default: 42).",
    )
    parser.add_argument(
        "--output-plot",
        default="data/active_learning_efficiency.png",
        metavar="PATH",
        help="Output path for the efficiency learning curve figure.",
    )
    parser.add_argument(
        "--json-summary",
        default="data/active_learning_summary.json",
        metavar="PATH",
        help="Output path for JSON summary of benchmark results.",
    )

    args = parser.parse_args(argv)

    data_path = Path(args.data)
    if not data_path.exists():
        logger.error("Dataset not found: %s", data_path)
        return 1

    df = pd.read_parquet(data_path)
    df_pool = df[df["split"] == "train"].copy()
    df_heldout = df[df["split"] == "held_out"].copy()

    logger.info("Loaded %d candidate pool rows, %d held-out rows", len(df_pool), len(df_heldout))

    benchmark = ActiveLearningBenchmark(
        df_pool=df_pool,
        df_heldout=df_heldout,
        target=args.target,
        threshold=args.threshold,
        target_accuracy=args.target_accuracy,
        n_init=args.n_init,
        n_steps=args.n_steps,
        seed=args.seed,
    )

    logger.info("Running active learning benchmark across BO, Random, and Grid...")
    summary = benchmark.run_all()

    # Generate efficiency learning curve figure
    plot_path = plot_active_learning_efficiency(
        results=summary.results,
        save_path=args.output_plot,
        target_accuracy=args.target_accuracy,
        threshold=args.threshold,
    )
    logger.info("Generated learning curves figure: %s", plot_path)

    # Save summary JSON
    summary_path = Path(args.json_summary)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    with open(summary_path, "w") as f:
        json.dump(summary.to_dict(), f, indent=2)
    logger.info("Saved summary JSON: %s", summary_path)

    # Output comparison table
    target_pct = int(args.target_accuracy * 100)
    print("\n" + "=" * 76)
    print(f"ACTIVE LEARNING EFFICIENCY BENCHMARK (SLO Threshold: {args.threshold:.0f} ms)")
    print(f"{'Strategy':<20} {'Exps to ' + str(target_pct) + '% Acc':<18} {'Final Accuracy':<16} {'Final RMSE':<12}")
    print("-" * 76)
    for strat_key in ("bo", "random", "grid"):
        res = summary.results[strat_key]
        exps_str = str(res.experiments_to_target) if res.experiments_to_target is not None else f">{res.history[-1].n_experiments}"
        acc_str = f"{res.final_accuracy * 100:.1f}%"
        rmse_str = f"{res.final_rmse:.1f} ms"
        strat_display = {
            "bo": "Bayesian Opt (SLO)",
            "random": "Random Search",
            "grid": "Grid Search",
        }[strat_key]
        print(f"{strat_display:<20} {exps_str:<18} {acc_str:<16} {rmse_str:<12}")
    print("=" * 76 + "\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
