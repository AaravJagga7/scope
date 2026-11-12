#!/usr/bin/env python
"""CLI for Task 14C: transfer of static-scaling constraints to HPA.

Runs the factorial HPA study (CPU target x max replicas x DB pool x workload),
evaluates every constraint from the validated static-scaling set on steady-state
and transition-phase rows, traces connection-invariant violations back to the
autoscaler action that caused them, classifies each constraint, and re-fits only
the shifted ones with the minimum number of GP-selected extra experiments.

Usage::

    python scripts/run_hpa_transfer.py
    python scripts/run_hpa_transfer.py --repeats 3 --base-rps 500 --refit-budget 5
    python scripts/run_hpa_transfer.py --no-refit --output-dir data
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

from scopex.discovery import (  # noqa: E402
    HPAStudyDesign,
    format_connection_report,
    format_transfer_table,
    run_hpa_transfer_study,
    save_study_outputs,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def _ints(values: list[str]) -> list[int]:
    return [int(value) for value in values]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--constraints",
        type=Path,
        default=_REPO_ROOT / "data" / "constraints_summary.json",
        help="Validated constraint set (loaded as-is; never retrained).",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=_REPO_ROOT / "data", help="Output directory."
    )
    parser.add_argument("--prefix", default="hpa_transfer", help="Output filename prefix.")
    parser.add_argument("--cpu-targets", nargs="+", default=["50", "70", "90"])
    parser.add_argument("--max-replicas", nargs="+", default=["4", "6", "10"])
    parser.add_argument("--db-pools", nargs="+", default=["50", "100"])
    parser.add_argument("--workloads", nargs="+", default=["ramp", "flash_sale", "sinusoidal"])
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--heldout-fraction", type=float, default=0.2)
    parser.add_argument("--duration", type=int, default=40)
    parser.add_argument("--base-rps", type=int, default=500)
    parser.add_argument("--refit-budget", type=int, default=5)
    parser.add_argument(
        "--no-refit", action="store_true", help="Skip GP-guided re-fit of shifted constraints."
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the summary JSON to stdout instead of the markdown table.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    design = HPAStudyDesign(
        cpu_targets=_ints(args.cpu_targets),
        max_replicas=_ints(args.max_replicas),
        db_pools=_ints(args.db_pools),
        workloads=list(args.workloads),
        repeats=args.repeats,
        heldout_fraction=args.heldout_fraction,
        duration=args.duration,
        base_rps=args.base_rps,
        seed=args.seed,
    )

    logger.info(
        "HPA study: %d cells x %d repeats (%d runs)",
        len(design.cells()),
        design.repeats,
        len(design.cells()) * design.repeats,
    )
    result = run_hpa_transfer_study(
        design=design,
        constraints_path=args.constraints,
        refit_budget=args.refit_budget,
        refit=not args.no_refit,
    )

    paths = save_study_outputs(result, output_dir=args.output_dir, prefix=args.prefix)

    if args.json:
        print(json.dumps(result.to_dict(), indent=2, default=str))
    else:
        print()
        print(format_transfer_table(result))
        print()
        print(format_connection_report(result))
        print()
        print(f"Saved: {paths['runs']}")
        print(f"Saved: {paths['rows']}")
        print(f"Saved: {paths['summary']}")
        print(f"Saved: {paths['table']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
