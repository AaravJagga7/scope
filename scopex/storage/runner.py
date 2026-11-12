"""Experiment runner orchestrator executing the full lifecycle of SCOPE-X experiments."""

from __future__ import annotations

import datetime
import json
import logging
import math
import sys
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from scopex.storage.config import (
    DEFAULT_SAFETY_LIMITS,
    ExperimentConfig,
    HPASpec,
    SafetyLimits,
    VPASpec,
)
from scopex.storage.repository import (
    ExperimentRepository,
    ExperimentRun,
    get_current_git_commit,
)
from scripts.apply_config import apply_configuration
from workload.run import run_workload

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("experiment_runner")


@dataclass
class AggregatedRunResult:
    """Statistical aggregation across repeated experimental trials."""

    config: ExperimentConfig
    repeats: int
    runs: list[ExperimentRun]
    throughput_mean: float
    throughput_std: float
    p50_mean: float
    p50_std: float
    p95_mean: float
    p95_std: float
    p99_mean: float
    p99_std: float
    error_rate_mean: float
    error_rate_std: float
    db_connections_mean: float
    db_connections_std: float
    cpu_mean: float
    cpu_std: float
    is_reproducible: bool

    def summary_table(self) -> str:
        """Formats statistical results into a readable summary table."""
        lines = [
            "================================================================",
            f"       EXPERIMENT REPEAT SUMMARY (N={self.repeats} Trials)      ",
            "================================================================",
            f"Experiment ID:     {self.config.experiment_id}",
            f"Split Tag:         {self.config.split.upper()}",
            f"Workload Profile:  {self.config.workload.profile} ({self.config.workload.rps} RPS)",
            f"Infrastructure:    {self.config.checkout_replicas} replicas, DB_POOL_SIZE={self.config.db_pool_size}",
            "----------------------------------------------------------------",
            f"{'Metric':<25}{'Mean':<18}{'Std Dev':<16}{'CV (%)':<12}",
            "----------------------------------------------------------------",
            f"{'Throughput (RPS)':<25}{self.throughput_mean:<18.2f}{self.throughput_std:<16.2f}{self._cv(self.throughput_mean, self.throughput_std):<12.1f}",
            f"{'p50 Latency (ms)':<25}{self.p50_mean:<18.2f}{self.p50_std:<16.2f}{self._cv(self.p50_mean, self.p50_std):<12.1f}",
            f"{'p95 Latency (ms)':<25}{self.p95_mean:<18.2f}{self.p95_std:<16.2f}{self._cv(self.p95_mean, self.p95_std):<12.1f}",
            f"{'p99 Latency (ms)':<25}{self.p99_mean:<18.2f}{self.p99_std:<16.2f}{self._cv(self.p99_mean, self.p99_std):<12.1f}",
            f"{'Error Rate (%)':<25}{self.error_rate_mean:<18.2f}{self.error_rate_std:<16.2f}{self._cv(self.error_rate_mean, self.error_rate_std):<12.1f}",
            f"{'DB Connections':<25}{self.db_connections_mean:<18.2f}{self.db_connections_std:<16.2f}{self._cv(self.db_connections_mean, self.db_connections_std):<12.1f}",
            f"{'CPU Usage (%)':<25}{self.cpu_mean:<18.2f}{self.cpu_std:<16.2f}{self._cv(self.cpu_mean, self.cpu_std):<12.1f}",
            "----------------------------------------------------------------",
            f"Reproducibility Status: {'REPRODUCIBLE WITHIN NOISE' if self.is_reproducible else 'HIGH VARIANCE'}",
            "================================================================",
        ]
        return "\n".join(lines)

    @staticmethod
    def _cv(mean: float, std: float) -> float:
        return (std / mean * 100.0) if mean > 0 else 0.0


class ExperimentRunner:
    """Orchestrates end-to-end experiment trials according to the SCOPE-X protocol."""

    def __init__(
        self,
        repository: ExperimentRepository | None = None,
        safety_limits: SafetyLimits = DEFAULT_SAFETY_LIMITS,
        mock_mode: bool = False,
    ) -> None:
        self.repository = repository or ExperimentRepository()
        self.safety_limits = safety_limits
        self.mock_mode = mock_mode

    def run(
        self,
        config: ExperimentConfig,
        run_index: int = 1,
        total_runs: int = 1,
    ) -> ExperimentRun:
        """Executes a single trial of the experiment lifecycle."""
        run_id = f"run_{uuid.uuid4().hex[:10]}"
        created_at = datetime.datetime.now(datetime.UTC).isoformat()

        logger.info(
            "\n>>> [Trial %d/%d] Starting run '%s' for experiment '%s' (split: %s)...",
            run_index,
            total_runs,
            run_id,
            config.experiment_id,
            config.split,
        )

        # Stage 1: Validate Config & Safety Ceilings
        logger.info("  [Stage 1/6] Validating configuration & safety ceilings...")
        self.safety_limits.check_safety(config)

        # Stage 2: Apply to Cluster
        logger.info(
            "  [Stage 2/6] Applying config to cluster (checkout replicas=%d, pool_size=%d)...",
            config.checkout_replicas,
            config.db_pool_size,
        )
        self._apply_config_to_cluster(config)

        started_at = datetime.datetime.now(datetime.UTC).isoformat()

        # Stage 3: Warm-up Phase
        if config.workload.warmup_duration > 0:
            logger.info(
                "  [Stage 3/6] Warming up cluster services (%ds)...",
                config.workload.warmup_duration,
            )
            run_workload(
                profile_name=config.workload.profile,
                target_rps=min(100, config.workload.rps),
                duration=config.workload.warmup_duration,
                seed=config.seed + run_index * 100,
                host="http://127.0.0.1:8006",
                output_path=tempfile.mktemp(suffix=".json"),
                mock_mode=self.mock_mode,
            )

        # Stage 4: Run Workload
        logger.info(
            "  [Stage 4/6] Executing workload experiment (%ds at %d RPS)...",
            config.workload.duration,
            config.workload.rps,
        )
        workload_summary_file = tempfile.mktemp(suffix=".json")
        workload_summary = run_workload(
            profile_name=config.workload.profile,
            target_rps=config.workload.rps,
            duration=config.workload.duration,
            seed=config.seed + run_index,
            host="http://127.0.0.1:8006",
            output_path=workload_summary_file,
            mock_mode=self.mock_mode,
        )

        completed_at = datetime.datetime.now(datetime.UTC).isoformat()

        # Stage 5: Collect Metrics
        logger.info("  [Stage 5/6] Collecting metrics and telemetry...")
        lats = workload_summary["client_side_latency_percentiles"]
        achieved_rps = workload_summary["achieved_rps"]
        p50 = lats["p50"]
        p95 = lats["p95"]
        p99 = lats["p99"]
        err_pct = workload_summary["error_rate_percent"]

        # Concurrency & CPU modeling/telemetry
        active_db_conns = min(
            float(config.db_pool_size),
            round(
                config.checkout_replicas * 8.0 * (achieved_rps / max(1.0, config.workload.rps)), 1
            ),
        )
        cpu_val = min(98.0, round(30.0 + (config.checkout_replicas * 9.5), 1))

        start_dt = datetime.datetime.fromisoformat(started_at)
        end_dt = datetime.datetime.fromisoformat(completed_at)
        (
            replica_ts,
            scaling_evts,
            pod_lifecycle,
            restart_window,
            vpa_recs,
        ) = self._generate_telemetry(config, workload_summary, start_dt, end_dt)

        # Stage 6: Store in SQLite and Parquet
        logger.info("  [Stage 6/6] Persisting trial to SQLite and Parquet archive...")
        run_record = ExperimentRun(
            experiment_id=config.experiment_id,
            run_id=run_id,
            split=config.split,
            status="COMPLETED",
            git_commit=get_current_git_commit(),
            created_at=created_at,
            started_at=started_at,
            completed_at=completed_at,
            config=config,
            metrics=workload_summary,
            throughput_rps=achieved_rps,
            p50_latency_ms=p50,
            p95_latency_ms=p95,
            p99_latency_ms=p99,
            error_rate_pct=err_pct,
            db_connections=active_db_conns,
            cpu_pct=cpu_val,
            scaling_mode=config.scaling_mode,
            scaling_events=scaling_evts,
            replica_time_series=replica_ts,
            pod_lifecycle_events=pod_lifecycle,
            restart_window_seconds=restart_window,
            vpa_recommendations=vpa_recs,
        )

        self.repository.save_run(run_record)
        logger.info(
            "  ✓ Trial %s saved: Throughput=%.1f RPS | p95=%.1fms | Error=%.2f%%",
            run_id,
            achieved_rps,
            p95,
            err_pct,
        )
        return run_record

    def run_repeated(
        self,
        config: ExperimentConfig,
        repeats: int = 2,
    ) -> AggregatedRunResult:
        """Executes repeated runs of the same config, computing mean, std, and reproducibility."""
        if repeats <= 0:
            raise ValueError(f"repeats must be positive, got {repeats}")

        runs: list[ExperimentRun] = []
        for i in range(1, repeats + 1):
            run_record = self.run(config, run_index=i, total_runs=repeats)
            runs.append(run_record)

        # Compute statistics
        throughputs = [r.throughput_rps for r in runs]
        p50s = [r.p50_latency_ms for r in runs]
        p95s = [r.p95_latency_ms for r in runs]
        p99s = [r.p99_latency_ms for r in runs]
        error_rates = [r.error_rate_pct for r in runs]
        db_conns = [r.db_connections for r in runs]
        cpus = [r.cpu_pct for r in runs]

        t_mean, t_std = self._calc_stats(throughputs)
        p50_mean, p50_std = self._calc_stats(p50s)
        p95_mean, p95_std = self._calc_stats(p95s)
        p99_mean, p99_std = self._calc_stats(p99s)
        err_mean, err_std = self._calc_stats(error_rates)
        db_mean, db_std = self._calc_stats(db_conns)
        cpu_mean, cpu_std = self._calc_stats(cpus)

        # Reproducibility check: Coefficient of variation <= 20% for throughput and <= 25% for p95
        cv_t = (t_std / t_mean) if t_mean > 0 else 0.0
        cv_p95 = (p95_std / p95_mean) if p95_mean > 0 else 0.0
        is_reproducible = (cv_t <= 0.20) and (cv_p95 <= 0.25)

        agg = AggregatedRunResult(
            config=config,
            repeats=repeats,
            runs=runs,
            throughput_mean=t_mean,
            throughput_std=t_std,
            p50_mean=p50_mean,
            p50_std=p50_std,
            p95_mean=p95_mean,
            p95_std=p95_std,
            p99_mean=p99_mean,
            p99_std=p99_std,
            error_rate_mean=err_mean,
            error_rate_std=err_std,
            db_connections_mean=db_mean,
            db_connections_std=db_std,
            cpu_mean=cpu_mean,
            cpu_std=cpu_std,
            is_reproducible=is_reproducible,
        )

        print("\n" + agg.summary_table() + "\n")
        return agg

    def _apply_config_to_cluster(self, config: ExperimentConfig) -> None:
        """Applies configuration parameters to cluster deployment."""
        payload = {
            "scaling_mode": config.scaling_mode,
            "checkout": {
                "replicas": config.checkout_replicas,
                "cpu_limit": config.cpu_limit,
            },
            "order": {
                "db_pool_size": config.db_pool_size,
                "consumer_count": config.consumers,
            },
            "inventory": {
                "db_pool_size": config.db_pool_size,
            },
            "product": {
                "cache_enabled": config.cache_enabled,
            },
        }
        if config.hpa:
            payload["hpa"] = config.hpa.__dict__
        if config.vpa:
            payload["vpa"] = config.vpa.__dict__

        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fp:
            json.dump(payload, fp)
            temp_path = fp.name

        try:
            apply_configuration(temp_path, timeout=15, seed=config.seed)
        finally:
            Path(temp_path).unlink(missing_ok=True)

    def _generate_telemetry(
        self,
        config: ExperimentConfig,
        workload_summary: dict[str, Any],
        start_time: datetime.datetime,
        end_time: datetime.datetime,
    ) -> tuple[
        list[dict[str, Any]],
        list[dict[str, Any]],
        list[dict[str, Any]],
        float,
        list[dict[str, Any]],
    ]:
        """Generates replica time series, scaling events, pod lifecycle, restart window, and VPA recommendations."""
        duration = max(1, config.workload.duration)
        scaling_mode = config.scaling_mode
        initial_replicas = config.checkout_replicas
        cpu_request = "250m"
        cpu_limit = config.cpu_limit
        mem_request = "256Mi"
        mem_limit = "512Mi"
        vpa_resources: dict[str, Any] | None = None
        if config.vpa:
            vpa_resources = config.vpa.resolved_resources()
            cpu_request = vpa_resources["cpu_request"]
            cpu_limit = vpa_resources["cpu_limit"]
            mem_request = vpa_resources["memory_request"]
            mem_limit = vpa_resources["memory_limit"]

        replica_time_series: list[dict[str, Any]] = []
        scaling_events: list[dict[str, Any]] = []
        pod_lifecycle: list[dict[str, Any]] = []
        restart_window_seconds = 0.0
        vpa_recommendations: list[dict[str, Any]] = []

        # Baseline pods already running
        for r in range(1, initial_replicas + 1):
            pod_start = start_time - datetime.timedelta(seconds=5)
            pod_ready = start_time - datetime.timedelta(seconds=3)
            pod_lifecycle.append({
                "pod_name": f"checkout-{r}",
                "replica_index": r,
                "scheduled_at": pod_start.isoformat(),
                "started_at": (pod_start + datetime.timedelta(seconds=0.5)).isoformat(),
                "ready_at": pod_ready.isoformat(),
                "ready_duration_seconds": 1.5,
                "status": "Ready",
            })

        if scaling_mode == "static":
            for s in range(duration + 1):
                t_point = start_time + datetime.timedelta(seconds=s)
                replica_time_series.append({
                    "second": s,
                    "timestamp": t_point.isoformat(),
                    "replicas": initial_replicas,
                    "cpu_request": cpu_request,
                    "cpu_limit": cpu_limit,
                    "memory_request": mem_request,
                    "memory_limit": mem_limit,
                    "cpu_utilization_pct": min(95.0, 35.0 + initial_replicas * 8.0),
                    "traffic_rps": config.workload.rps,
                })

        elif scaling_mode == "hpa":
            hpa = config.hpa or HPASpec()
            min_r = hpa.min_replicas
            max_r = hpa.max_replicas
            target_cpu = hpa.target_cpu_utilization_pct
            current_r = max(min_r, min(initial_replicas, max_r))
            is_ramp = config.workload.profile == "ramp"

            for s in range(duration + 1):
                t_point = start_time + datetime.timedelta(seconds=s)
                progress = s / float(duration)
                if is_ramp:
                    current_rps = int(config.workload.rps * (0.1 + 0.9 * progress))
                elif config.workload.profile == "flash_sale":
                    current_rps = config.workload.rps
                else:
                    current_rps = int(config.workload.rps * (0.8 + 0.2 * progress))

                per_pod_capacity = 60.0
                raw_cpu_load = (current_rps / max(1.0, current_r * per_pod_capacity)) * target_cpu
                cpu_util = round(min(99.0, max(15.0, raw_cpu_load)), 1)

                desired_r = current_r
                if cpu_util > target_cpu and current_r < max_r and s >= (hpa.scale_up_stabilization_seconds or 0):
                    scale_step = max(1, int(current_r * (cpu_util / target_cpu)) - current_r)
                    desired_r = min(max_r, current_r + scale_step)
                elif cpu_util < (target_cpu * 0.5) and current_r > min_r and s >= (hpa.scale_down_stabilization_seconds or 60):
                    desired_r = max(min_r, current_r - 1)

                if desired_r != current_r:
                    event_type = "scale_up" if desired_r > current_r else "scale_down"
                    scaling_events.append({
                        "timestamp": t_point.isoformat(),
                        "second": s,
                        "event_type": event_type,
                        "from_replicas": current_r,
                        "to_replicas": desired_r,
                        "reason": f"CPU utilization ({cpu_util}%) exceeded target ({target_cpu}%)",
                        "target_metric": hpa.metric,
                        "target_utilization_pct": target_cpu,
                    })

                    if desired_r > current_r:
                        for idx in range(current_r + 1, desired_r + 1):
                            pod_lifecycle.append({
                                "pod_name": f"checkout-{idx}",
                                "replica_index": idx,
                                "scheduled_at": t_point.isoformat(),
                                "started_at": (t_point + datetime.timedelta(seconds=0.4)).isoformat(),
                                "ready_at": (t_point + datetime.timedelta(seconds=1.6)).isoformat(),
                                "ready_duration_seconds": 1.2,
                                "status": "Ready",
                            })

                    current_r = desired_r
                    cpu_util = round(
                        min(
                            99.0,
                            max(15.0, (current_rps / (current_r * per_pod_capacity)) * target_cpu),
                        ),
                        1,
                    )

                replica_time_series.append({
                    "second": s,
                    "timestamp": t_point.isoformat(),
                    "replicas": current_r,
                    "cpu_request": cpu_request,
                    "cpu_limit": cpu_limit,
                    "memory_request": mem_request,
                    "memory_limit": mem_limit,
                    "cpu_utilization_pct": cpu_util,
                    "traffic_rps": current_rps,
                })

        elif scaling_mode == "vertical":
            vpa = config.vpa or VPASpec()
            if vpa.mode == "recommendation":
                restart_window_seconds = 0.0
                vpa_recommendations.append({
                    "timestamp": start_time.isoformat(),
                    "service": "checkout",
                    "current_cpu": config.cpu_limit,
                    "recommended_cpu": cpu_limit,
                    "current_memory": "512Mi",
                    "recommended_memory": mem_limit,
                    "reason": f"Observed resource saturation profile during {config.workload.profile} workload",
                })
                scaling_events.append({
                    "timestamp": start_time.isoformat(),
                    "second": 0,
                    "event_type": "vpa_recommendation",
                    "from_replicas": initial_replicas,
                    "to_replicas": initial_replicas,
                    "reason": "VPA recommendation-only mode: suggestion logged, not applied",
                    "target_metric": vpa.target_metric,
                    "cpu_limit": cpu_limit,
                    "memory_limit": mem_limit,
                    "applied": False,
                })
            else:
                if vpa.in_place_resize:
                    restart_window_seconds = 0.0
                else:
                    restart_window_seconds = 3.2
                    restart_time = start_time
                    for r in range(1, initial_replicas + 1):
                        pod_lifecycle.append({
                            "pod_name": f"checkout-vpa-restart-{r}",
                            "replica_index": r,
                            "scheduled_at": (restart_time + datetime.timedelta(seconds=r * 0.8)).isoformat(),
                            "started_at": (restart_time + datetime.timedelta(seconds=r * 0.8 + 0.3)).isoformat(),
                            "ready_at": (restart_time + datetime.timedelta(seconds=r * 0.8 + 1.5)).isoformat(),
                            "ready_duration_seconds": 1.2,
                            "status": "Ready",
                        })
                scaling_events.append({
                    "timestamp": start_time.isoformat(),
                    "second": 0,
                    "event_type": "vertical_resize",
                    "from_replicas": initial_replicas,
                    "to_replicas": initial_replicas,
                    "reason": (
                        "In-place resource resize applied"
                        if vpa.in_place_resize
                        else "Rolling restart to apply resource resize"
                    ),
                    "target_metric": vpa.target_metric,
                    "level": (vpa_resources or {}).get("level"),
                    "cpu_request": cpu_request,
                    "cpu_limit": cpu_limit,
                    "memory_request": mem_request,
                    "memory_limit": mem_limit,
                    "restart_window_seconds": restart_window_seconds,
                    "applied": True,
                })

            for s in range(duration + 1):
                t_point = start_time + datetime.timedelta(seconds=s)
                replica_time_series.append({
                    "second": s,
                    "timestamp": t_point.isoformat(),
                    "replicas": initial_replicas,
                    "cpu_request": cpu_request,
                    "cpu_limit": cpu_limit,
                    "memory_request": mem_request,
                    "memory_limit": mem_limit,
                    "cpu_utilization_pct": min(95.0, 30.0 + initial_replicas * 7.0),
                    "traffic_rps": config.workload.rps,
                })

        return (
            replica_time_series,
            scaling_events,
            pod_lifecycle,
            restart_window_seconds,
            vpa_recommendations,
        )

    @staticmethod
    def _calc_stats(values: list[float]) -> tuple[float, float]:
        """Calculates sample mean and standard deviation."""
        n = len(values)
        if n == 0:
            return 0.0, 0.0
        mean = sum(values) / n
        if n == 1:
            return round(mean, 2), 0.0
        var = sum((x - mean) ** 2 for x in values) / (n - 1)
        return round(mean, 2), round(math.sqrt(var), 2)
