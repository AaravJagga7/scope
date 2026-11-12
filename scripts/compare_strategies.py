#!/usr/bin/env python
"""CLI script to benchmark scaling strategies (Fixed, HPA, Queue, SCOPE-X).

Runs all comparison strategies through an identical test harness under the
same workload profile and random seed.

Usage::

    # Default flash sale comparison
    python scripts/compare_strategies.py --profile flash_sale

    # Custom RPS and duration
    python scripts/compare_strategies.py --profile flash_sale --rps 800 --duration 60

    # JSON export
    python scripts/compare_strategies.py --profile flash_sale --json-output data/strategy_comparison_summary.json
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

# Ensure repo root is importable when run directly
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scopex.decision import run_comparison

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("compare_strategies")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run comparative evaluation of autoscaling strategies under identical workloads and seeds."
    )
    parser.add_argument(
        "--profile",
        "-p",
        choices=["flash_sale", "normal", "ramp", "viral_product", "slow_payment_api"],
        default="flash_sale",
        help="Workload traffic profile (default: flash_sale).",
    )
    parser.add_argument(
        "--rps",
        type=int,
        default=700,
        help="Peak target requests per second (default: 700).",
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=60,
        help="Workload duration in seconds (default: 60).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for reproducible traffic trace and simulation noise (default: 42).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output raw JSON to stdout instead of formatted table.",
    )
    parser.add_argument(
        "--json-output",
        type=str,
        default=None,
        metavar="PATH",
        help="Save result JSON to specified file path.",
    )

    args = parser.parse_args(argv)

    try:
        report = run_comparison(
            profile=args.profile,
            peak_rps=args.rps,
            duration_seconds=args.duration,
            seed=args.seed,
        )
    except Exception as exc:
        logger.error("Comparison execution failed: %s", exc)
        return 1

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print("\n" + report.format_table() + "\n")

    if args.json_output:
        out_path = Path(args.json_output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(report.to_dict(), f, indent=2)
        print(f"✓ Comparison summary saved to: {out_path}\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())

