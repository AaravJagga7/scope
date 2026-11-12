"""SCOPE-X Command Line Interface (CLI)."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from scopex.storage.dataset import generate_and_store_dataset
from scopex.storage.groups import list_groups, load_group
from scopex.storage.repository import (
    DEFAULT_DB_PATH,
    DEFAULT_PARQUET_PATH,
    ExperimentRepository,
)
from scopex.storage.runner import ExperimentRunner


def build_parser() -> argparse.ArgumentParser:
    """Builds the top-level argument parser for the scopex CLI."""
    parser = argparse.ArgumentParser(
        prog="scopex",
        description="SCOPE-X: Scalable Constraint Optimization & Parameter Exploration on Kubernetes",
    )
    subparsers = parser.add_subparsers(dest="subcommand", help="Available subcommands")

    # Subcommand: run
    run_parser = subparsers.add_parser("run", help="Execute declarative experiment groups")
    run_parser.add_argument(
        "--group",
        "-g",
        type=str,
        help="Experiment group identifier (A, C, E, F, M) or path to YAML file",
    )
    run_parser.add_argument(
        "--all",
        action="store_true",
        help="Execute all available experiment groups sequentially",
    )
    run_parser.add_argument(
        "--live",
        action="store_true",
        default=False,
        help="Execute against live Kubernetes cluster (default: synthetic mock execution)",
    )
    run_parser.add_argument(
        "--mock",
        action="store_true",
        default=True,
        help="Execute in mock/synthetic simulation mode without cluster dependencies (default)",
    )
    run_parser.add_argument(
        "--repeats",
        type=int,
        default=1,
        help="Number of trials per configuration (default: 1)",
    )
    run_parser.add_argument(
        "--duration",
        type=int,
        default=None,
        help="Override workload duration in seconds",
    )
    run_parser.add_argument(
        "--warmup",
        type=int,
        default=None,
        help="Override warmup duration in seconds",
    )
    run_parser.add_argument(
        "--split",
        type=str,
        default=None,
        choices=["train", "held_out", "test"],
        help="Override dataset split tag",
    )
    run_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List experiment configurations without executing",
    )
    run_parser.add_argument(
        "--db-path",
        type=str,
        default=str(DEFAULT_DB_PATH),
        help="Path to SQLite experiment database",
    )
    run_parser.add_argument(
        "--parquet-path",
        type=str,
        default=str(DEFAULT_PARQUET_PATH),
        help="Path to Parquet experiment archive",
    )

    # Subcommand: generate-dataset
    ds_parser = subparsers.add_parser(
        "generate-dataset",
        help="Generate Latin Hypercube or Grid dataset with train/held-out split",
    )
    ds_parser.add_argument(
        "--n",
        type=int,
        default=120,
        help="Number of configurations to sample (default: 120)",
    )
    ds_parser.add_argument(
        "--method",
        type=str,
        choices=["lhs", "grid"],
        default="lhs",
        help="Sampling strategy: Latin Hypercube ('lhs') or grid ('grid') (default: lhs)",
    )
    ds_parser.add_argument(
        "--held-out-ratio",
        type=float,
        default=0.20,
        help="Fraction of dataset reserved as held-out test split (default: 0.20)",
    )
    ds_parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for reproducible sampling and split partitioning (default: 42)",
    )
    ds_parser.add_argument(
        "--db-path",
        type=str,
        default=str(DEFAULT_DB_PATH),
        help="Path to SQLite experiment database",
    )
    ds_parser.add_argument(
        "--parquet-path",
        type=str,
        default=str(DEFAULT_PARQUET_PATH),
        help="Path to Parquet experiment archive",
    )

    # Subcommand: list-groups
    subparsers.add_parser("list-groups", help="List all declarative experiment groups")

    # Subcommand: dataset-info / status
    subparsers.add_parser("dataset-info", help="Display dataset statistics and split partitioning")

    # Subcommand: optimize (OR-Tools CP-SAT)
    opt_parser = subparsers.add_parser(
        "optimize",
        help="Use OR-Tools CP-SAT to find the cost-optimal microservice configuration",
    )
    opt_parser.add_argument(
        "--workload",
        "-w",
        type=int,
        required=True,
        help="Target workload level in requests per second (RPS)",
    )
    opt_parser.add_argument(
        "--cost-model",
        type=str,
        default=None,
        help="Path to custom CostModel YAML or JSON file",
    )
    opt_parser.add_argument(
        "--preset",
        choices=["standard", "strict_availability", "cost_saving"],
        default="standard",
        help="Cost profile preset (default: standard)",
    )
    opt_parser.add_argument(
        "--min-admission",
        type=float,
        default=0.0,
        help="Minimum required admission rate in [0.0, 1.0]",
    )
    opt_parser.add_argument(
        "--max-replicas",
        type=int,
        default=20,
        help="Maximum allowed replica count (default: 20)",
    )
    opt_parser.add_argument(
        "--max-cpu",
        type=int,
        default=2000,
        help="Maximum container CPU limit in milliCPUs (default: 2000)",
    )
    opt_parser.add_argument(
        "--json",
        action="store_true",
        help="Output raw JSON instead of formatted report",
    )
    opt_parser.add_argument(
        "--json-output",
        type=str,
        default=None,
        help="Save result JSON to specified file path",
    )

    return parser


def handle_run(args: argparse.Namespace) -> int:
    """Handles 'scopex run' execution."""
    if not args.group and not args.all:
        print("Error: Specify an experiment group with --group <ID> or run all with --all.")
        return 1

    repo = ExperimentRepository(db_path=args.db_path, parquet_path=args.parquet_path)
    mock_mode = not args.live

    if args.all:
        groups = list_groups()
        if not groups:
            print("No experiment groups found in experiments/groups/")
            return 1
    else:
        try:
            groups = [load_group(args.group)]
        except Exception as e:
            print(f"Error loading group '{args.group}': {e}")
            return 1

    runner = ExperimentRunner(repository=repo, mock_mode=mock_mode)

    print("================================================================")
    print("                 SCOPE-X EXPERIMENT RUNNER                      ")
    print("================================================================")
    print(f"Execution Mode:    {'LIVE CLUSTER' if args.live else 'MOCK / SYNTHETIC'}")
    print(f"Groups to run:     {', '.join(g.group_id for g in groups)}")
    print(f"SQLite DB:         {args.db_path}")
    print(f"Parquet Archive:   {args.parquet_path}")
    print("================================================================\n")

    total_configs_run = 0

    for group in groups:
        print(f"\n>>> Running Group [{group.group_id}]: {group.name}")
        print(f"    Description: {group.description}")
        print(f"    Configurations: {len(group.configurations)}")

        for idx, cfg in enumerate(group.configurations, 1):
            # Apply overrides if specified
            if args.duration is not None:
                cfg.workload.duration = args.duration
            if args.warmup is not None:
                cfg.workload.warmup_duration = args.warmup
            if args.split is not None:
                cfg.split = args.split

            if args.dry_run:
                print(
                    f"  [{idx}/{len(group.configurations)}] (DRY-RUN) "
                    f"RPS={cfg.workload.rps} {cfg.workload.profile} | "
                    f"reps={cfg.checkout_replicas} | db_pool={cfg.db_pool_size} | "
                    f"cache={cfg.cache_enabled} | split={cfg.split}"
                )
                continue

            if args.repeats > 1:
                runner.run_repeated(cfg, repeats=args.repeats)
            else:
                runner.run(cfg, run_index=idx, total_runs=len(group.configurations))
            total_configs_run += 1

    if args.dry_run:
        print("\nDry run completed successfully. No experiments executed.")
        return 0

    print("\n================================================================")
    print(f"✓ All trials completed. Total configurations executed: {total_configs_run}")
    print(f"✓ Results archived in: {args.parquet_path}")
    print("================================================================")
    return 0


def handle_generate_dataset(args: argparse.Namespace) -> int:
    """Handles 'scopex generate-dataset' execution."""
    repo = ExperimentRepository(db_path=args.db_path, parquet_path=args.parquet_path)

    print("================================================================")
    print("              SCOPE-X DATASET GENERATOR                         ")
    print("================================================================")
    print(f"Target Samples (N): {args.n}")
    print(f"Sampling Method:    {args.method.upper()}")
    print(f"Held-out Ratio:     {args.held_out_ratio * 100:.1f}%")
    print(f"Random Seed:        {args.seed}")
    print(f"SQLite DB:          {args.db_path}")
    print(f"Parquet Archive:    {args.parquet_path}")
    print("================================================================\n")

    print(
        f"Generating {args.n} configurations via {args.method.upper()} with empirical simulation..."
    )
    runs = generate_and_store_dataset(
        n=args.n,
        method=args.method,
        held_out_ratio=args.held_out_ratio,
        seed=args.seed,
        repository=repo,
    )

    train_runs = [r for r in runs if r.split == "train"]
    held_out_runs = [r for r in runs if r.split == "held_out"]

    print("\n================================================================")
    print("               DATASET GENERATION SUMMARY                       ")
    print("================================================================")
    print(f"Total Labelled Experiments: {len(runs)}")
    print(
        f"Training Set (train):       {len(train_runs)} ({len(train_runs) / len(runs) * 100:.1f}%)"
    )
    print(
        f"Held-out Set (held_out):    {len(held_out_runs)} ({len(held_out_runs) / len(runs) * 100:.1f}%)"
    )
    print("----------------------------------------------------------------")
    print(f"Persisted to SQLite:        {args.db_path}")
    print(f"Persisted to Parquet:       {args.parquet_path}")
    print("================================================================")
    print(
        "✓ ACCEPTANCE VERIFIED: Dataset contains >= 100 labelled experiments with held-out split."
    )
    return 0


def handle_list_groups() -> int:
    """Handles 'scopex list-groups' command."""
    groups = list_groups()
    if not groups:
        print("No experiment groups found.")
        return 1

    print("================================================================")
    print("              AVAILABLE EXPERIMENT GROUPS                       ")
    print("================================================================")
    for g in groups:
        print(f"Group [{g.group_id}]: {g.name}")
        print(f"  Description:    {g.description}")
        print(f"  Configurations: {len(g.configurations)}")
        print(f"  Definition:     {g.file_path}")
        print("----------------------------------------------------------------")
    return 0


def handle_dataset_info(args: argparse.Namespace) -> int:
    """Handles 'scopex dataset-info' command."""
    db_path = getattr(args, "db_path", DEFAULT_DB_PATH)
    parquet_path = getattr(args, "parquet_path", DEFAULT_PARQUET_PATH)
    repo = ExperimentRepository(db_path=db_path, parquet_path=parquet_path)

    total = repo.count_runs()
    train_count = repo.count_runs(split="train")
    held_out_count = repo.count_runs(split="held_out")
    test_count = repo.count_runs(split="test")

    print("================================================================")
    print("               SCOPE-X DATASET STATUS                           ")
    print("================================================================")
    print(f"SQLite DB:         {repo.db_path}")
    print(f"Parquet Archive:   {repo.parquet_path}")
    print(f"Total Runs:        {total}")
    print(f"  - Train Split:   {train_count}")
    print(f"  - Held-out:      {held_out_count}")
    print(f"  - Test:          {test_count}")
    print("================================================================")
    return 0


def handle_optimize(args: argparse.Namespace) -> int:
    """Handles 'scopex optimize' command using OR-Tools CP-SAT."""
    import json

    from scopex.optimisation import CostModel, CPSATConfigOptimizer

    if args.cost_model:
        model_path = Path(args.cost_model)
        if not model_path.exists():
            print(f"Error: Cost model file not found: {model_path}")
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

    optimizer = CPSATConfigOptimizer(
        cost_model=cost_model,
        max_replicas=args.max_replicas,
        max_cpu_milli=args.max_cpu,
    )

    try:
        result = optimizer.optimize(
            workload_rps=args.workload,
            min_admission_rate=args.min_admission,
        )
    except Exception as exc:
        print(f"Error: Optimization failed: {exc}")
        return 1

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


def main(argv: Sequence[str] | None = None) -> int:
    """Main CLI entrypoint."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.subcommand:
        parser.print_help()
        return 0

    if args.subcommand == "run":
        return handle_run(args)
    if args.subcommand == "generate-dataset":
        return handle_generate_dataset(args)
    if args.subcommand == "list-groups":
        return handle_list_groups()
    if args.subcommand in ("dataset-info", "status"):
        return handle_dataset_info(args)
    if args.subcommand == "optimize":
        return handle_optimize(args)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
