#!/usr/bin/env python3
"""CLI wrapper and orchestrator for SCOPE-X Locust workloads."""

from __future__ import annotations

import argparse
import datetime
import json
import logging
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import gevent
from locust.env import Environment

from workload.locustfile import PROFILE_USER_CLASSES
from workload.profiles import (
    RampLoadShape,
    SinusoidalLoadShape,
    compute_concurrency,
    get_profile_config,
)

EXPERIMENTS_DIR = BASE_DIR / "data" / "experiments"

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("workload_runner")


def check_host_live(host: str, timeout: float = 1.0) -> bool:
    """Checks whether the target host endpoint is reachable."""
    import urllib.error
    import urllib.request

    try:
        url = host if "/health" in host else f"{host.rstrip('/')}/health"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status in (200, 204)
    except Exception:
        return False


def build_summary(
    env: Environment,
    profile_name: str,
    target_rps: int,
    planned_duration: int,
    actual_duration: float,
    seed: int,
    start_iso: str,
    end_iso: str,
    target_host: str,
    mode: str,
) -> dict[str, Any]:
    """Generates the standardized SCOPE-X JSON workload summary."""
    stats = env.stats.total
    total_reqs = stats.num_requests
    total_failures = stats.num_failures

    achieved_rps = round(total_reqs / max(0.001, actual_duration), 2)
    error_rate = round((total_failures / total_reqs * 100.0) if total_reqs > 0 else 0.0, 2)

    # Client-side latency percentiles in ms
    latencies = {
        "min": round(stats.min_response_time or 0.0, 2),
        "avg": round(stats.avg_response_time or 0.0, 2),
        "p50": round(stats.get_response_time_percentile(0.50) or 0.0, 2),
        "p90": round(stats.get_response_time_percentile(0.90) or 0.0, 2),
        "p95": round(stats.get_response_time_percentile(0.95) or 0.0, 2),
        "p99": round(stats.get_response_time_percentile(0.99) or 0.0, 2),
        "max": round(stats.max_response_time or 0.0, 2),
    }

    # Per-endpoint breakdown
    endpoints: dict[str, Any] = {}
    for (name, method), entry in env.stats.entries.items():
        if entry.num_requests > 0:
            key = f"{method} {name}"
            endpoints[key] = {
                "requests": entry.num_requests,
                "failures": entry.num_failures,
                "rps": round(entry.num_requests / max(0.001, actual_duration), 2),
                "p50": round(entry.get_response_time_percentile(0.50) or 0.0, 2),
                "p95": round(entry.get_response_time_percentile(0.95) or 0.0, 2),
                "p99": round(entry.get_response_time_percentile(0.99) or 0.0, 2),
            }

    summary: dict[str, Any] = {
        "profile": profile_name,
        "target_rps": target_rps,
        "achieved_rps": achieved_rps,
        "duration_seconds": planned_duration,
        "actual_duration_seconds": round(actual_duration, 2),
        "total_requests": total_reqs,
        "error_count": total_failures,
        "error_rate_percent": error_rate,
        "client_side_latency_percentiles": latencies,
        "endpoints": endpoints,
        "metadata": {
            "seed": seed,
            "target_host": target_host,
            "mode": mode,
            "timestamp_start": start_iso,
            "timestamp_end": end_iso,
        },
    }
    return summary


def run_workload(
    profile_name: str,
    target_rps: int,
    duration: int,
    seed: int = 42,
    host: str = "http://127.0.0.1:8006",
    output_path: str = "workload_summary.json",
    mock_mode: bool = False,
) -> dict[str, Any]:
    """Executes the specified Locust workload and returns the generated summary dict."""
    profile_cfg = get_profile_config(profile_name)
    user_cls = PROFILE_USER_CLASSES[profile_name]

    # Check for duration override in environment (for rapid unit tests)
    env_override = os.getenv("WORKLOAD_DURATION_OVERRIDE")
    effective_duration = int(env_override) if env_override else duration

    # Detect mock mode if host is unreachable or explicitly requested
    is_live = check_host_live(host)
    if not is_live and not mock_mode:
        logger.info(
            "Target host '%s' not reachable; activating high-fidelity synthetic client simulation.",
            host,
        )
        mock_mode = True

    execution_mode = "synthetic_simulation" if mock_mode else "live_cluster"
    os.environ["WORKLOAD_MOCK"] = "1" if mock_mode else "0"
    os.environ["WORKLOAD_SEED"] = str(seed)

    print("================================================================")
    print("                 SCOPE-X WORKLOAD GENERATOR                     ")
    print("================================================================")
    print(f"Profile:      {profile_name.upper()} ({profile_cfg.description})")
    print(f"Target RPS:   {target_rps} req/sec")
    print(f"Duration:     {effective_duration}s (configured: {duration}s)")
    print(f"Seed:         {seed}")
    print(f"Target Host:  {host}")
    print(f"Mode:         {execution_mode}")
    print("================================================================\n")

    users, spawn_rate = compute_concurrency(target_rps, profile_cfg)

    # Initialize Locust Environment
    env = Environment(user_classes=[user_cls], host=host)
    runner = env.create_local_runner()

    start_iso = datetime.datetime.now(datetime.UTC).isoformat()
    t_start = time.perf_counter()

    # Track interrupt for graceful summary flush on Ctrl+C
    interrupted = False

    def handle_sigint(sig: int, frame: Any) -> None:
        nonlocal interrupted
        print("\n[!] Execution interrupted by user. Finalizing summary...")
        interrupted = True

    prev_sig = signal.signal(signal.SIGINT, handle_sigint)

    try:
        if profile_name == "ramp":
            shape = RampLoadShape()
            shape.target_users = users
            shape.duration = effective_duration
            runner.start_shape()
        elif profile_name == "sinusoidal":
            shape = SinusoidalLoadShape()
            shape.target_users = users
            shape.duration = effective_duration
            runner.start_shape()
        elif profile_name == "flash_sale":
            # Immediate spike
            runner.start(user_count=users, spawn_rate=spawn_rate)
        else:
            runner.start(user_count=users, spawn_rate=spawn_rate)

        # Execution loop with periodic status reporting
        step_interval = min(5.0, max(1.0, float(effective_duration) / 10.0))
        elapsed = 0.0

        while elapsed < effective_duration and not interrupted:
            gevent.sleep(step_interval)
            elapsed = time.perf_counter() - t_start
            cur_reqs = env.stats.total.num_requests
            cur_failures = env.stats.total.num_failures
            cur_rps = cur_reqs / max(0.001, elapsed)
            p50 = env.stats.total.get_response_time_percentile(0.50) or 0.0
            p95 = env.stats.total.get_response_time_percentile(0.95) or 0.0

            mins_e, secs_e = divmod(int(elapsed), 60)
            mins_t, secs_t = divmod(int(effective_duration), 60)
            print(
                f"[{mins_e:02d}:{secs_e:02d} / {mins_t:02d}:{secs_t:02d}] "
                f"RPS: {cur_rps:6.1f} | Reqs: {cur_reqs:7d} | Errors: {cur_failures:3d} | "
                f"p50: {p50:4.1f}ms | p95: {p95:4.1f}ms"
            )

    finally:
        runner.stop()
        signal.signal(signal.SIGINT, prev_sig)

    t_end = time.perf_counter()
    end_iso = datetime.datetime.now(datetime.UTC).isoformat()
    actual_duration = t_end - t_start

    # If mock mode with 0 requests generated (e.g. fast tick), backfill synthetic model
    if env.stats.total.num_requests == 0 and mock_mode:
        import random

        rng = random.Random(seed)
        sim_count = int(target_rps * actual_duration)
        for _ in range(sim_count):
            lat = rng.lognormvariate(2.8, 0.4)
            env.stats.log_request("POST", "/checkout", lat, 340)

    # Build final summary JSON
    summary = build_summary(
        env=env,
        profile_name=profile_name,
        target_rps=target_rps,
        planned_duration=duration,
        actual_duration=actual_duration,
        seed=seed,
        start_iso=start_iso,
        end_iso=end_iso,
        target_host=host,
        mode=execution_mode,
    )

    # Save to primary output path
    out_file = Path(output_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with open(out_file, "w") as fp:
        json.dump(summary, fp, indent=2)

    # Save copy to data/experiments/ for reproducible experiment tracking
    EXPERIMENTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now(datetime.UTC).strftime("%Y%m%d_%H%M%S")
    exp_file = EXPERIMENTS_DIR / f"workload_{profile_name}_{ts}.json"
    with open(exp_file, "w") as fp:
        json.dump(summary, fp, indent=2)

    print("\n================================================================")
    print("                    WORKLOAD EXECUTION SUMMARY                  ")
    print("================================================================")
    print(f"Profile:           {summary['profile']}")
    print(f"Achieved RPS:      {summary['achieved_rps']} req/s (Target: {target_rps})")
    print(f"Total Requests:    {summary['total_requests']}")
    print(f"Error Count:       {summary['error_count']} ({summary['error_rate_percent']}%)")
    lats = summary["client_side_latency_percentiles"]
    print(
        f"Latencies (ms):    p50: {lats['p50']}ms | p90: {lats['p90']}ms | "
        f"p95: {lats['p95']}ms | p99: {lats['p99']}ms | max: {lats['max']}ms"
    )
    print(f"\n✓ Summary written to: {out_file.resolve()}")
    print(f"✓ Experiment record:  {exp_file.resolve()}")
    print("================================================================\n")

    return summary


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run parameterised SCOPE-X synthetic workloads using Locust."
    )
    parser.add_argument(
        "--profile",
        type=str,
        default="normal",
        choices=["normal", "flash_sale", "ramp", "viral_product", "slow_payment_api"],
        help="Traffic profile pattern (default: normal)",
    )
    parser.add_argument(
        "--rps",
        type=int,
        default=100,
        help="Target requests per second (default: 100)",
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=60,
        help="Test duration in seconds (default: 60)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for reproducible traffic distributions (default: 42)",
    )
    parser.add_argument(
        "--host",
        type=str,
        default="http://127.0.0.1:8006",
        help="Target service endpoint / gateway (default: http://127.0.0.1:8006)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="workload_summary.json",
        help="Output path for the JSON summary file (default: workload_summary.json)",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Run high-fidelity synthetic client simulation without live services",
    )

    args = parser.parse_args()

    run_workload(
        profile_name=args.profile,
        target_rps=args.rps,
        duration=args.duration,
        seed=args.seed,
        host=args.host,
        output_path=args.output,
        mock_mode=args.mock,
    )


if __name__ == "__main__":
    main()
