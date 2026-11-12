#!/usr/bin/env python
"""CLI to execute the CEGIS verification and testbed validation loop.

1. Encodes candidate constraints and requirements in Z3.
2. Queries Z3 for counterexamples violating the SLO that constraints allow.
3. Deploys and validates configurations on the real testbed.
4. Refines constraints upon confirmed real violations and repeats.
5. Stops when verified in the model or budget exhausted.

Usage::

    python scripts/run_verification.py
    python scripts/run_verification.py --budget 5 --max-p95 500.0
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

from scopex.discovery import (
    DomainBounds,
    SLORequirements,
    VerificationLoop,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
)
logger = logging.getLogger("run_verification")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run CEGIS formal verification and real testbed validation loop."
    )
    parser.add_argument(
        "--budget",
        type=int,
        default=5,
        help="Maximum verification iterations / testbed experiment budget (default: 5).",
    )
    parser.add_argument(
        "--max-p95",
        type=float,
        default=500.0,
        help="SLO max p95 latency in ms (default: 500.0).",
    )
    parser.add_argument(
        "--max-error",
        type=float,
        default=1.0,
        help="SLO max error rate percentage (default: 1.0).",
    )
    parser.add_argument(
        "--max-db-conns",
        type=float,
        default=50.0,
        help="SLO max active DB connections (default: 50.0).",
    )
    parser.add_argument(
        "--json-summary",
        default="data/verification_summary.json",
        metavar="PATH",
        help="Path for saving structured JSON summary of all iterations.",
    )

    args = parser.parse_args(argv)

    requirements = SLORequirements(
        max_p95_latency_ms=args.max_p95,
        max_error_rate_pct=args.max_error,
        max_db_connections=args.max_db_conns,
    )
    domain = DomainBounds()

    loop = VerificationLoop(
        requirements=requirements,
        domain=domain,
        budget=args.budget,
    )

    summary = loop.run()

    if args.json_summary:
        summary_path = Path(args.json_summary)
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        with open(summary_path, "w") as f:
            json.dump(summary.to_dict(), f, indent=2)
        print(f"\n✓ Verification summary written to: {summary_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
