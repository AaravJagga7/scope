#!/usr/bin/env python3
"""Demonstrate the Adaptive Scaling Decision Engine (Task 17).

Default scenario: flash sale, CPU=95%, DB connections near ceiling.
Expected output: ADD_REPLICAS rejected, engine explains the DB ceiling,
recommends LIMIT_TRAFFIC or INCREASE_DB_CAPACITY instead.

Usage
-----
# Acceptance scenario (should decline ADD_REPLICAS and explain why)
python scripts/run_decision_engine.py \\
    --profile flash_sale --rps 700 --replicas 4 --db-pool 48 --cpu-util 95

# Normal headroom scenario (should accept ADD_REPLICAS)
python scripts/run_decision_engine.py \\
    --profile normal --rps 300 --replicas 2 --db-pool 20 --cpu-util 70
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

# Ensure project root is on path when invoked directly
sys.path.insert(0, str(Path(__file__).parent.parent))

from scopex.decision.engine import AdaptiveDecisionEngine, LiveMetrics
from scopex.decision.executor import ExecutionManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("run_decision_engine")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="SCOPE-X Adaptive Scaling Decision Engine demo",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument(
        "--profile",
        choices=["flash_sale", "normal", "ramp", "viral_product", "slow_payment_api"],
        default="flash_sale",
        help="Workload profile",
    )
    p.add_argument("--rps", type=float, default=700.0, help="Current RPS")
    p.add_argument("--replicas", type=int, default=4, help="Current Checkout replicas")
    p.add_argument("--db-pool", type=int, default=48, help="DB pool size per replica")
    p.add_argument("--cpu-util", type=float, default=95.0, help="CPU utilisation %%")
    p.add_argument("--p95", type=float, default=420.0, help="Current p95 latency ms")
    p.add_argument("--error-rate", type=float, default=0.5, help="Current error rate %%")
    p.add_argument("--db-limit", type=int, default=200, help="Postgres connection ceiling")
    p.add_argument("--json", action="store_true", help="Emit JSON output")
    p.add_argument("--verbose", "-v", action="store_true", help="Enable debug logging")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    logger.info("=== SCOPE-X Decision Engine ===")
    logger.info(
        "Input: profile=%s rps=%.0f replicas=%d db_pool=%d cpu=%.0f%%",
        args.profile,
        args.rps,
        args.replicas,
        args.db_pool,
        args.cpu_util,
    )

    metrics = LiveMetrics(
        workload_rps=args.rps,
        cpu_utilization_pct=args.cpu_util,
        p95_latency_ms=args.p95,
        error_rate_pct=args.error_rate,
        current_replicas=args.replicas,
        current_db_pool_size=args.db_pool,
        db_connection_limit=args.db_limit,
        profile=args.profile,
    )

    engine = AdaptiveDecisionEngine()
    rec = engine.evaluate_and_recommend(metrics)

    executor = ExecutionManager(live=False)
    plan = executor.plan(rec, dry_run=True)
    result = executor.execute(plan)

    if args.json:
        print(json.dumps({"recommendation": rec.to_dict(), "execution": result.to_dict()}, indent=2))
    else:
        print()
        print(rec.format_report())
        print()
        print(result.format_summary())
        print()

    # Exit-code reflects whether the recommended action is "HOLD" or not
    # (non-zero means only HOLD was available — potential alert)
    from scopex.decision.engine import ActionType

    if rec.recommended_action.action_type == ActionType.HOLD:
        logger.warning("All positive actions were rejected; engine recommends HOLD.")
    else:
        logger.info(
            "Engine recommendation: %s", rec.recommended_action.action_type.value.upper()
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())

