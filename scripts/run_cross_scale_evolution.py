#!/usr/bin/env python
"""CLI to execute cross-scale constraint evolution (2 replicas -> 4 replicas).

1. Retrieves old constraints learned at 2 replicas.
2. Predicts which constraints should still hold, relax, or tighten.
3. Selects GP uncertainty verification points.
4. Classifies constraints: unchanged, tightened, relaxed, invalidated, new.
5. Updates affected constraints.
6. Benchmarks Incremental Evolution vs Full Rediscovery from scratch.

Usage::

    python scripts/run_cross_scale_evolution.py
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
    CrossScaleEvolutionEngine,
    format_evolution_report,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
)
logger = logging.getLogger("cross_scale_evolution")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run cross-scale constraint evolution benchmark."
    )
    parser.add_argument(
        "--data",
        default="data/experiments.parquet",
        metavar="PATH",
        help="Path to experiments Parquet file (default: data/experiments.parquet).",
    )
    parser.add_argument(
        "--source-replicas",
        type=int,
        default=2,
        help="Source replica count (default: 2).",
    )
    parser.add_argument(
        "--target-replicas",
        type=int,
        default=4,
        help="Target scaled replica count (default: 4).",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=500.0,
        help="SLO latency threshold in ms (default: 500.0).",
    )
    parser.add_argument(
        "--json-summary",
        default="data/cross_scale_evolution_summary.json",
        metavar="PATH",
        help="Output JSON summary path.",
    )

    args = parser.parse_args(argv)

    data_path = Path(args.data)
    if not data_path.exists():
        logger.error("Dataset not found: %s", data_path)
        return 1

    df = pd.read_parquet(data_path)
    df_source = df[df["checkout_replicas"] == args.source_replicas].copy()
    df_target = df[df["checkout_replicas"] == args.target_replicas].copy()

    # Split target data into training candidate pool and held-out test set
    df_target_pool = df_target[df_target["split"] == "train"].copy()
    df_target_test = df_target[df_target["split"] == "held_out"].copy()

    # If held-out test set is small, supplement with held-out simulation
    if len(df_target_test) < 10:
        # Sample an evaluation slice
        df_target_test = df_target.sample(n=min(20, len(df_target)), random_state=42)

    logger.info("Loaded %d source experiments (R=%d), %d target pool rows (R=%d)",
                len(df_source), args.source_replicas, len(df_target_pool), args.target_replicas)

    engine = CrossScaleEvolutionEngine(
        source_replicas=args.source_replicas,
        target_replicas=args.target_replicas,
        threshold=args.threshold,
    )

    result = engine.run_benchmark_comparison(
        df_source=df_source,
        df_target_pool=df_target_pool,
        df_test=df_target_test,
        budgets=[2, 4, 6, 8, 12, 16, 20],
    )

    report = format_evolution_report(result)
    print("\n" + report + "\n")

    if args.json_summary:
        summary_path = Path(args.json_summary)
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        with open(summary_path, "w") as f:
            json.dump(result.to_dict(), f, indent=2)
        print(f"✓ Summary written to: {summary_path}\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())

