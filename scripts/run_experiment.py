#!/usr/bin/env python3
"""CLI utility for executing reproducible SCOPE-X experiments with repeated runs."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from scopex.storage import (
    DEFAULT_DB_PATH,
    DEFAULT_PARQUET_PATH,
    ExperimentConfig,
    ExperimentRepository,
    ExperimentRunner,
    WorkloadSpec,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run SCOPE-X experiment trial(s) with persistence and reproducibility reporting."
    )
    parser.add_argument(
        "--replicas",
        type=int,
        default=2,
        help="Checkout service replicas (default: 2)",
    )
    parser.add_argument(
        "--db-pool-size",
        type=int,
        default=50,
        help="Database pool size limit (default: 50)",
    )
    parser.add_argument(
        "--rps",
        type=int,
        default=100,
        help="Target workload requests per second (default: 100)",
    )
    parser.add_argument(
        "--profile",
        type=str,
        default="normal",
        choices=["normal", "flash_sale", "ramp", "viral_product", "slow_payment_api"],
        help="Workload traffic profile (default: normal)",
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=3,
        help="Workload run duration in seconds (default: 3)",
    )
    parser.add_argument(
        "--warmup",
        type=int,
        default=1,
        help="Warmup duration in seconds (default: 1)",
    )
    parser.add_argument(
        "--repeats",
        type=int,
        default=2,
        help="Number of repeated executions for reproducibility assessment (default: 2)",
    )
    parser.add_argument(
        "--split",
        type=str,
        default="train",
        choices=["train", "held_out", "test"],
        help="Dataset split tag (default: train)",
    )
    parser.add_argument(
        "--cpu-limit",
        type=str,
        default="500m",
        help="CPU limit per replica (default: 500m)",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Disable Redis product cache",
    )
    parser.add_argument(
        "--consumers",
        type=int,
        default=1,
        help="Order service consumer concurrency (default: 1)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed (default: 42)",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Run in synthetic simulation mode",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=DEFAULT_DB_PATH,
        help=f"SQLite database destination (default: {DEFAULT_DB_PATH})",
    )
    parser.add_argument(
        "--parquet-path",
        type=Path,
        default=DEFAULT_PARQUET_PATH,
        help=f"Parquet archive destination (default: {DEFAULT_PARQUET_PATH})",
    )

    args = parser.parse_args()

    workload = WorkloadSpec(
        rps=args.rps,
        profile=args.profile,
        duration=args.duration,
        warmup_duration=args.warmup,
    )
    config = ExperimentConfig(
        workload=workload,
        checkout_replicas=args.replicas,
        db_pool_size=args.db_pool_size,
        cpu_limit=args.cpu_limit,
        cache_enabled=not args.no_cache,
        consumers=args.consumers,
        seed=args.seed,
        split=args.split,
    )

    repo = ExperimentRepository(db_path=args.db_path, parquet_path=args.parquet_path)
    runner = ExperimentRunner(repository=repo, mock_mode=args.mock)

    print("================================================================")
    print("                 SCOPE-X EXPERIMENT RUNNER                      ")
    print("================================================================")
    print(f"Experiment ID:     {config.experiment_id}")
    print(f"Split Tag:         {config.split}")
    print(f"Repeats:           {args.repeats}")
    print(f"Workload:          {config.workload.profile} ({config.workload.rps} RPS)")
    print(
        f"Infrastructure:    {config.checkout_replicas} replicas, DB_POOL_SIZE={config.db_pool_size}"
    )
    print(f"SQLite DB:         {args.db_path}")
    print(f"Parquet Archive:   {args.parquet_path}")
    print("================================================================\n")

    agg_result = runner.run_repeated(config, repeats=args.repeats)

    if agg_result.is_reproducible:
        print("✓ ACCEPTANCE VERIFIED: Repeated runs demonstrate reproducible metrics within noise.")
        sys.exit(0)
    else:
        print("! Notice: Metrics exceeded expected noise thresholds.")
        sys.exit(1)


if __name__ == "__main__":
    main()
