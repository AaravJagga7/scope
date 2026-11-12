#!/usr/bin/env python
"""CLI to learn interpretable operational constraints from experiment datasets.

Uses decision trees and sparse regression to extract rules of the form:
    conditions => SLO violation

Evaluates and ranks rules by precision and recall on the held-out test split,
and outputs a human-readable list with Z3 expressions and support metrics.

Usage::

    python scripts/learn_constraints.py
    python scripts/learn_constraints.py --threshold 500.0 --top-n 10
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
    evaluate_and_rank_constraints,
    format_constraints_report,
    learn_constraints,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("learn_constraints")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Learn interpretable SLO boundary constraints from data."
    )
    parser.add_argument(
        "--data",
        default="data/experiments.parquet",
        metavar="PATH",
        help="Path to experiments Parquet file (default: data/experiments.parquet).",
    )
    parser.add_argument(
        "--target-col",
        default="p95_latency_ms",
        help="Metric column defining SLO (default: p95_latency_ms).",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=500.0,
        help="SLO violation threshold (default: 500.0 ms).",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=10,
        help="Number of top ranked constraints to display (default: 10).",
    )
    parser.add_argument(
        "--json-summary",
        default="data/constraints_summary.json",
        metavar="PATH",
        help="Destination path for JSON summary.",
    )

    args = parser.parse_args(argv)

    data_path = Path(args.data)
    if not data_path.exists():
        logger.error("Dataset not found: %s", data_path)
        return 1

    df = pd.read_parquet(data_path)
    df_train = df[df["split"] == "train"].copy()
    df_heldout = df[df["split"] == "held_out"].copy()

    logger.info(
        "Loaded dataset: %d train rows, %d held-out rows",
        len(df_train),
        len(df_heldout),
    )

    # 1. Learn candidate constraints on train set
    logger.info(
        "Learning constraints from train data (SLO: %s > %.1f)...",
        args.target_col,
        args.threshold,
    )
    candidates = learn_constraints(
        df_train=df_train,
        target_col=args.target_col,
        threshold=args.threshold,
    )
    logger.info("Extracted %d candidate rules across methods", len(candidates))

    # 2. Evaluate and rank strictly on held-out test data
    logger.info("Evaluating and ranking constraints on held-out test data...")
    ranked = evaluate_and_rank_constraints(
        constraints=candidates,
        df_heldout=df_heldout,
        target_col=args.target_col,
        threshold=args.threshold,
    )

    # 3. Print human-readable report
    report = format_constraints_report(
        constraints=ranked,
        top_n=args.top_n,
        title=f"TOP OPERATIONAL CONSTRAINTS ({args.target_col} > {args.threshold:.0f} ms)",
    )
    print("\n" + report + "\n")

    # 4. Save JSON summary
    if args.json_summary:
        summary_path = Path(args.json_summary)
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_payload = {
            "target_col": args.target_col,
            "threshold": args.threshold,
            "train_samples": len(df_train),
            "heldout_samples": len(df_heldout),
            "total_rules": len(ranked),
            "top_constraints": [c.to_dict() for c in ranked[: args.top_n]],
        }
        with open(summary_path, "w") as f:
            json.dump(summary_payload, f, indent=2)
        logger.info("Saved JSON summary to %s", summary_path)

    return 0


if __name__ == "__main__":
    sys.exit(main())

