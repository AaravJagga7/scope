#!/usr/bin/env python
"""CLI tool to optimize microservice configuration using OR-Tools CP-SAT.

Minimises operational infrastructure cost subject to validated constraints and SLOs.

Usage::

    # Basic optimization for 500 RPS workload
    python scripts/optimize_config.py --workload 500

    # With strict 100% admission requirement
    python scripts/optimize_config.py --workload 800 --min-admission 1.0

    # Using custom cost profile
    python scripts/optimize_config.py --workload 600 --preset strict_availability

    # Exporting JSON summary
    python scripts/optimize_config.py --workload 500 --json-output data/optimal_config_500rps.json
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

from scopex.optimisation import CostModel, CPSATConfigOptimizer

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("optimize_config")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Optimize microservice deployment configuration using OR-Tools CP-SAT."
    )
    parser.add_argument(
        "--workload",
        "-w",
        type=int,
        required=True,
        help="Incoming traffic workload in requests per second (RPS).",
    )
    parser.add_argument(
        "--cost-model",
        type=str,
        default=None,
        metavar="PATH",
        help="Path to custom CostModel YAML or JSON file.",
    )
    parser.add_argument(
        "--preset",
        choices=["standard", "strict_availability", "cost_saving"],
        default="standard",
        help="Cost profile preset (default: standard).",
    )
    parser.add_argument(
        "--min-admission",
        type=float,
        default=0.0,
        help="Minimum required admission rate in [0.0, 1.0] (default: 0.0).",
    )
    parser.add_argument(
        "--max-replicas",
        type=int,
        default=20,
        help="Maximum allowed replica count (default: 20).",
    )
    parser.add_argument(
        "--max-cpu",
        type=int,
        default=2000,
        help="Maximum container CPU limit in milliCPUs (default: 2000).",
    )
    parser.add_argument(
        "--force-cache",
        action="store_true",
        default=None,
        help="Force caching layer enabled.",
    )
    parser.add_argument(
        "--force-no-cache",
        action="store_true",
        default=None,
        help="Force caching layer disabled.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output raw JSON to stdout instead of formatted report.",
    )
    parser.add_argument(
        "--json-output",
        type=str,
        default=None,
        metavar="PATH",
        help="Save result JSON to specified file path.",
    )

    args = parser.parse_args(argv)

    # 1. Initialize Cost Model
    if args.cost_model:
        model_path = Path(args.cost_model)
        if not model_path.exists():
            logger.error("Cost model file not found: %s", model_path)
            return 1
        if model_path.suffix in (".yaml", ".yml"):
            cost_model = CostModel.from_yaml(model_path)
        else:
            cost_model = CostModel.from_json(model_path)
    else:
        if args.preset == "strict_availability":
            cost_model = CostModel.strict_availability()
        elif args.preset == "cost_saving":
            cost_model = CostModel.cost_saving()
        else:
            cost_model = CostModel.standard_cloud()

    # 2. Determine cache override
    force_cache: bool | None = None
    if args.force_cache:
        force_cache = True
    elif args.force_no_cache:
        force_cache = False

    # 3. Instantiate Optimizer
    optimizer = CPSATConfigOptimizer(
        cost_model=cost_model,
        max_replicas=args.max_replicas,
        max_cpu_milli=args.max_cpu,
    )

    # 4. Solve
    try:
        result = optimizer.optimize(
            workload_rps=args.workload,
            min_admission_rate=args.min_admission,
            force_cache=force_cache,
        )
    except Exception as exc:
        logger.error("Optimization failed: %s", exc)
        return 1

    # 5. Output
    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print("\n" + result.format_report() + "\n")

    if args.json_output:
        out_path = Path(args.json_output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(result.to_dict(), f, indent=2)
        print(f"✓ Optimization result saved to: {out_path}\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
