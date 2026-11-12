"""Task 14D: behaviour of the validated constraints under vertical scaling.

The static constraint set (learned at a fixed 500m CPU limit) is held fixed and
re-evaluated across a factorial vertical-scaling study::

    CPU limit {0.5, 1, 2} cores x memory {512Mi, 1Gi, 2Gi}
      x replicas {2, 4} x DB pool {50, 100} x cache {on, off}
      x offered load spanning the learned SLO boundary

The study answers five questions:

1. How does per-replica capacity depend on the CPU limit? ``mu(s) = mu_0 * s**alpha``
   is fitted by log-log regression on *measured* saturation throughput, with a
   confidence interval for ``alpha`` and a test of linear scaling (``alpha = 1``).
2. Is each learned constraint unchanged, shifted, disappeared or new?
3. Does the CPU-saturation constraint stop binding at higher CPU limits, and does
   the database constraint stay put?
4. Does the number of DB connections per replica depend on CPU or memory limits?
5. Before any constraint is declared *disappeared*, targeted extra experiments are
   run as close to its old boundary as the region allows, and the closest point
   tested is reported.

Model honesty
-------------
Everything is simulated. Per-replica CPU capacity is **not** a power law in the
simulator. It comes from a per-request service-time model with a serial part
(event loop / GIL, which does not speed up with more cores) and a parallel CPU part
(which does)::

    mu(s) = 1 / (T_SERIAL + T_PARALLEL / s)

The power law fitted in step 1 is therefore a deliberately misspecified,
interpretable approximation estimated from noisy measured throughput. The
generating values are recorded in the output so the fit can be checked against
them. Memory limits act through a working-set model: in-flight and queued requests
use memory, GC pressure reduces capacity, and exceeding the limit OOM-kills the pod,
which restarts after ``restart_seconds``. CFS throttling is modelled as a function
of per-pod CPU utilisation and inflates tail latency.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import logging
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats

from scopex.discovery.constraints import Constraint, learn_decision_tree_constraints
from scopex.discovery.hpa_transfer import (
    _SIMPLE_CLAUSE,
    CLASS_DISAPPEARED,
    BoundEstimate,
    PhaseMetrics,
    _classify,
    _compile_evaluator,
    _json_default,
    _linear_spec,
    _mask_overlap,
    estimate_bound,
    evaluate_constraint,
    load_validated_constraints,
)
from scopex.storage.config import ExperimentConfig, WorkloadSpec
from scopex.storage.dataset import simulate_system_metrics

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DEFAULT_STATIC_PATH = BASE_DIR / "data" / "experiments.parquet"
DEFAULT_CONSTRAINTS_PATH = BASE_DIR / "data" / "constraints_summary.json"

VIOLATION_THRESHOLD_MS = 500.0
CLASS_NEW = "new (vertical)"

# --- Generating model (documented so the alpha fit can be audited) -----------
#: CPU limit used when the static constraint set was learned.
BASELINE_CPU_CORES = 0.5
#: Calibrated static capacity at the baseline limit (matches simulate_system_metrics).
BASELINE_RPS_PER_REPLICA = 70.0
#: Per-request serial service time (seconds); does not shrink with more cores.
T_SERIAL = 0.0020
#: Per-request parallelisable CPU time (core-seconds), calibrated so mu(0.5) = 70 rps.
T_PARALLEL = (1.0 / BASELINE_RPS_PER_REPLICA - T_SERIAL) * BASELINE_CPU_CORES
#: Resident memory of an idle checkout pod (MiB).
BASE_WORKING_SET_MIB = 180.0
#: Memory per in-flight or queued request (MiB).
MIB_PER_INFLIGHT = 1.5
#: Requests a pod will queue before timing them out.
QUEUE_LIMIT_PER_POD = 400.0
#: DB capacity constants mirrored from simulate_system_metrics (flash_sale mix).
DB_RPS_PER_CONNECTION = 16.0
DB_CACHE_FACTOR = 2.2


def cpu_capacity(cpu_cores: float) -> float:
    """Per-replica throughput limit (rps) for a CPU limit, before memory effects."""
    return 1.0 / (T_SERIAL + T_PARALLEL / max(1e-6, float(cpu_cores)))


def generating_alpha(cpu_levels: list[float]) -> float:
    """Least-squares log-log slope of the generating capacity curve over *cpu_levels*."""
    x = np.log(np.asarray(cpu_levels, dtype=float))
    y = np.log([cpu_capacity(c) for c in cpu_levels])
    return float(np.polyfit(x, y, 1)[0])


def gc_capacity_factor(memory_pressure: float) -> float:
    """Capacity multiplier from GC pressure (working set / memory limit)."""
    return 1.0 - 0.35 * float(np.clip((memory_pressure - 0.7) / 0.3, 0.0, 1.0))


def throttled_periods_pct(utilisation: float, cpu_cores: float) -> float:
    """Share of CFS periods throttled. Sub-core limits throttle bursts sooner."""
    base = float(np.clip((utilisation - 0.55) / 0.45, 0.0, 1.0)) ** 1.5
    return 100.0 * base * min(1.0, 0.5 + 0.25 / max(1e-6, cpu_cores))


def _db_capacity(pool: int, cache: bool) -> float:
    return pool * DB_RPS_PER_CONNECTION * (DB_CACHE_FACTOR if cache else 1.0)


def _fmt_cpu(cores: float) -> str:
    millis = int(round(cores * 1000))
    return f"{millis // 1000}" if millis % 1000 == 0 else f"{millis}m"


def _fmt_mem(mib: int) -> str:
    return f"{mib // 1024}Gi" if mib % 1024 == 0 else f"{mib}Mi"


def add_derived_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Adds ratio features used by the mechanism constraints (works on any study's rows)."""
    out = df.copy()
    out["rps_per_replica"] = out["target_rps"] / out["checkout_replicas"].clip(lower=1)
    out["rps_per_pool_conn"] = out["target_rps"] / out["db_pool_size"].clip(lower=1)
    if "cpu_cores" not in out.columns:
        out["cpu_cores"] = BASELINE_CPU_CORES
    return out


# ---------------------------------------------------------------------------
# Design
# ---------------------------------------------------------------------------


@dataclass
class VerticalStudyDesign:
    """Factorial design for the vertical-scaling constraint study."""

    cpu_cores: list[float] = field(default_factory=lambda: [0.5, 1.0, 2.0])
    memory_mib: list[int] = field(default_factory=lambda: [512, 1024, 2048])
    replicas: list[int] = field(default_factory=lambda: [2, 4])
    db_pools: list[int] = field(default_factory=lambda: [50, 100])
    # Cache off is included so the database constraint can actually bind: with the
    # cache on, DB capacity (>= 1760 rps) is never reached in this region.
    cache: list[bool] = field(default_factory=lambda: [True, False])
    # Dense around the learned 487.5 rps boundary, wide enough to cross the CPU
    # boundary of every (replicas, CPU) cell (140 .. ~790 rps).
    rps_levels: list[int] = field(
        default_factory=lambda: [150, 250, 350, 425, 475, 500, 550, 650, 800, 1000]
    )
    workload: str = "flash_sale"
    repeats: int = 2
    heldout_fraction: float = 0.2
    duration: int = 30
    warmup_seconds: int = 3
    restart_seconds: int = 10
    settle_seconds: int = 3
    consumers: int = 2
    seed: int = 42

    def cells(self) -> list[dict[str, Any]]:
        return [
            {
                "cpu_cores": cpu,
                "memory_mib": mem,
                "checkout_replicas": rep,
                "db_pool_size": pool,
                "cache_enabled": cache,
                "target_rps": rps,
            }
            for cpu, mem, rep, pool, cache, rps in itertools.product(
                self.cpu_cores,
                self.memory_mib,
                self.replicas,
                self.db_pools,
                self.cache,
                self.rps_levels,
            )
        ]

    def split_cells(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        cells = self.cells()
        rng = np.random.default_rng(self.seed)
        order = np.arange(len(cells))
        rng.shuffle(order)
        n_heldout = max(1, int(round(len(cells) * self.heldout_fraction)))
        heldout_idx = set(order[:n_heldout].tolist())
        train = [cells[i] for i in range(len(cells)) if i not in heldout_idx]
        heldout = [cells[i] for i in sorted(heldout_idx)]
        return train, heldout


def vertical_tested_region(design: VerticalStudyDesign) -> dict[str, Any]:
    return {
        "cpu_limit_cores": design.cpu_cores,
        "memory_limit_mib": design.memory_mib,
        "checkout_replicas": design.replicas,
        "db_pool_sizes": design.db_pools,
        "cache_enabled": design.cache,
        "target_rps": [min(design.rps_levels), max(design.rps_levels)],
        "rps_levels": design.rps_levels,
        "workload": f"{design.workload} request mix, constant offered load",
        "duration_seconds": design.duration,
        "repeats": design.repeats,
    }


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------


def _run_seed(cell: dict[str, Any], design: VerticalStudyDesign, repeat: int) -> int:
    key = "|".join(str(cell[k]) for k in sorted(cell)) + f"|{repeat}"
    return design.seed + int(hashlib.sha256(key.encode()).hexdigest()[:8], 16)


def simulate_vertical_run(
    cell: dict[str, Any],
    design: VerticalStudyDesign,
    repeat: int = 0,
    seed: int | None = None,
) -> tuple[dict[str, Any], pd.DataFrame]:
    """Simulates one fixed-resource run second by second.

    Records pod restarts (OOM kills), CFS throttling, working-set memory, DB
    connections and latency per second, with each second labelled ``steady`` or
    ``transition`` (warm-up, or within a restart window plus settle time).
    """
    run_seed = seed if seed is not None else _run_seed(cell, design, repeat)
    rng = np.random.default_rng(run_seed)

    cpu = float(cell["cpu_cores"])
    mem = int(cell["memory_mib"])
    replicas = int(cell["checkout_replicas"])
    pool = int(cell["db_pool_size"])
    cache = bool(cell["cache_enabled"])
    rps = float(cell["target_rps"])
    run_id = (
        f"vpa_{_fmt_cpu(cpu)}_{_fmt_mem(mem)}_r{replicas}_p{pool}_"
        f"{'c' if cache else 'nc'}_{int(rps)}_rep{repeat}"
    )

    restart_until: list[int] = []
    backlog = 0.0
    working_set = BASE_WORKING_SET_MIB
    transition_until = -1
    restarts = 0
    events: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []

    for second in range(design.duration):
        restart_until = [t for t in restart_until if t > second]
        ready = replicas - len(restart_until)

        pressure = working_set / mem
        cap_pod = cpu_capacity(cpu) * gc_capacity_factor(pressure)

        if ready <= 0:
            metrics = {
                "p95": 10_000.0,
                "p99": 10_000.0,
                "error": 100.0,
                "db": 0.0,
                "cpu_util": 0.0,
                "achieved": 0.0,
            }
            utilisation = 0.0
        else:
            cfg = ExperimentConfig(
                workload=WorkloadSpec(
                    rps=max(1, int(round(rps))), profile=design.workload, duration=1
                ),
                checkout_replicas=ready,
                db_pool_size=pool,
                cache_enabled=cache,
                consumers=design.consumers,
                cpu_limit=_fmt_cpu(cpu),
                seed=int(rng.integers(0, 2**31 - 1)),
            )
            raw = simulate_system_metrics(cfg, frontend_rps_per_replica=cap_pod)
            lats = raw["client_side_latency_percentiles"]
            utilisation = min(1.0, (rps / ready) / cap_pod)
            throttle = throttled_periods_pct(utilisation, cpu)
            # CFS throttling stalls requests mid-flight, inflating the tail. The static
            # simulator was calibrated at the 500m baseline, so its latencies already
            # contain baseline throttling: apply only the change relative to 500m.
            base_throttle = throttled_periods_pct(utilisation, BASELINE_CPU_CORES)
            tail = (1.0 + 0.5 * throttle / 100.0) / (1.0 + 0.5 * base_throttle / 100.0)
            metrics = {
                "p95": float(lats["p95"]) * tail,
                "p99": float(lats["p99"]) * tail,
                "error": float(raw["error_rate_percent"]),
                "db": float(raw["db_connections"]),
                "cpu_util": float(raw["cpu_pct"]),
                "achieved": float(raw["achieved_rps"]),
            }

        throttle = throttled_periods_pct(utilisation, cpu) if ready > 0 else 0.0

        # Queue dynamics drive memory: overload accumulates a per-pod backlog.
        oom_kills = 0
        if ready > 0:
            system_cap = min(ready * cap_pod, _db_capacity(pool, cache))
            backlog = float(np.clip(backlog + (rps - system_cap) / ready, 0.0, QUEUE_LIMIT_PER_POD))
            p50_s = max(0.005, metrics["p95"] / 2.5 / 1000.0)
            inflight = (metrics["achieved"] / ready) * p50_s + backlog
            pod_sets = (
                BASE_WORKING_SET_MIB
                + MIB_PER_INFLIGHT * inflight
                + rng.normal(0.0, 12.0, size=ready)
            )
            working_set = float(np.mean(pod_sets))
            oom_kills = int(np.sum(pod_sets > mem))
            if oom_kills:
                for _ in range(oom_kills):
                    restart_until.append(second + design.restart_seconds)
                restarts += oom_kills
                transition_until = max(
                    transition_until, second + design.restart_seconds + design.settle_seconds
                )
                events.append(
                    {
                        "second": second,
                        "event_type": "oom_kill",
                        "pods": oom_kills,
                        "working_set_mib": round(float(np.max(pod_sets)), 1),
                        "memory_limit_mib": mem,
                    }
                )
        else:
            backlog = 0.0
            working_set = BASE_WORKING_SET_MIB

        in_transition = second < design.warmup_seconds or second <= transition_until
        rows.append(
            {
                "run_id": run_id,
                "second": second,
                "phase": "transition" if in_transition else "steady",
                "target_rps": rps,
                "checkout_replicas": replicas,
                "ready_replicas": max(0, ready),
                "db_pool_size": pool,
                "cache_enabled": int(cache),
                "consumers": design.consumers,
                "workload_profile": design.workload,
                "cpu_cores": cpu,
                "memory_mib": mem,
                "cpu_limit": _fmt_cpu(cpu),
                "memory_limit": _fmt_mem(mem),
                "p95_latency_ms": round(metrics["p95"], 1),
                "p99_latency_ms": round(metrics["p99"], 1),
                "error_rate_pct": metrics["error"],
                "achieved_rps": metrics["achieved"],
                "throughput_per_ready_replica": (
                    round(metrics["achieved"] / ready, 2) if ready > 0 else 0.0
                ),
                "db_connections": metrics["db"],
                "db_conns_per_replica": round(metrics["db"] / ready, 3) if ready > 0 else 0.0,
                "cpu_utilization_pct": metrics["cpu_util"],
                "throttled_periods_pct": round(throttle, 2),
                "working_set_mib": round(working_set, 1),
                "memory_pressure": round(working_set / mem, 3),
                "oom_kills": oom_kills,
                "pod_restarts_cum": restarts,
                "slo_violated": metrics["p95"] > VIOLATION_THRESHOLD_MS,
            }
        )

    frame = add_derived_columns(pd.DataFrame(rows))
    summary = {
        **{k: (int(v) if isinstance(v, bool) else v) for k, v in cell.items()},
        "run_id": run_id,
        "repeat": repeat,
        "seed": run_seed,
        "pod_restarts": restarts,
        "oom_events": events,
        "max_throttled_pct": round(float(frame["throttled_periods_pct"].max()), 2),
        "mean_throttled_pct": round(float(frame["throttled_periods_pct"].mean()), 2),
        "peak_working_set_mib": round(float(frame["working_set_mib"].max()), 1),
        "p95_latency_ms_median": round(float(frame["p95_latency_ms"].median()), 1),
        "slo_violation_seconds": int(frame["slo_violated"].sum()),
        "steady_rows": int((frame["phase"] == "steady").sum()),
        "transition_rows": int((frame["phase"] == "transition").sum()),
    }
    return summary, frame


def build_vertical_dataset(
    design: VerticalStudyDesign,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    train_cells, heldout_cells = design.split_cells()
    summaries: list[dict[str, Any]] = []
    frames: list[pd.DataFrame] = []
    for cell, split in [
        *[(c, "train") for c in train_cells],
        *[(c, "held_out") for c in heldout_cells],
    ]:
        for repeat in range(design.repeats):
            summary, frame = simulate_vertical_run(cell, design, repeat=repeat)
            summary["split"] = split
            frame["split"] = split
            summaries.append(summary)
            frames.append(frame)
    return pd.DataFrame(summaries), pd.concat(frames, ignore_index=True)


# ---------------------------------------------------------------------------
# 1. Capacity vs CPU: mu(s) = mu_0 * s^alpha
# ---------------------------------------------------------------------------


@dataclass
class OLSResult:
    names: list[str]
    coef: np.ndarray
    se: np.ndarray
    dof: int
    r2: float

    def ci(self, name: str, level: float = 0.95) -> tuple[float, float]:
        i = self.names.index(name)
        t = stats.t.ppf(0.5 + level / 2.0, self.dof)
        return float(self.coef[i] - t * self.se[i]), float(self.coef[i] + t * self.se[i])

    def get(self, name: str) -> float:
        return float(self.coef[self.names.index(name)])

    def stderr(self, name: str) -> float:
        return float(self.se[self.names.index(name)])


def _ols(X: np.ndarray, y: np.ndarray, names: list[str]) -> OLSResult:
    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=float)
    coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ coef
    dof = max(1, len(y) - X.shape[1])
    sigma2 = float(resid @ resid) / dof
    cov = sigma2 * np.linalg.pinv(X.T @ X)
    ss_tot = float(((y - y.mean()) ** 2).sum())
    r2 = 1.0 - float(resid @ resid) / ss_tot if ss_tot > 0 else 0.0
    return OLSResult(names, coef, np.sqrt(np.diag(cov)), dof, r2)


def capacity_observations(rows: pd.DataFrame) -> pd.DataFrame:
    """One measured per-replica capacity per run, from CPU-saturated steady seconds.

    A second counts as a capacity measurement when the offered load exceeds what
    was served by >5% (saturated), the frontend reports >=93% CPU (CPU-bound, not
    DB-bound), all replicas are ready and no restart is in progress.
    """
    sat = rows[
        (rows["phase"] == "steady")
        & (rows["ready_replicas"] == rows["checkout_replicas"])
        & (rows["target_rps"] > 1.05 * rows["achieved_rps"])
        & (rows["cpu_utilization_pct"] >= 93.0)
    ]
    if sat.empty:
        return pd.DataFrame()
    obs = (
        sat.groupby(["run_id", "cpu_cores", "memory_mib", "checkout_replicas"])
        .agg(
            capacity_rps=("throughput_per_ready_replica", "median"),
            seconds=("second", "count"),
            memory_pressure=("memory_pressure", "mean"),
        )
        .reset_index()
    )
    return obs[obs["seconds"] >= 3]


def fit_capacity_power_law(obs: pd.DataFrame, n_boot: int = 1000, seed: int = 42) -> dict[str, Any]:
    """Fits log mu = log mu_0 + alpha log s (+ memory-level offsets) and tests alpha = 1."""
    if obs.empty or obs["cpu_cores"].nunique() < 2:
        return {"estimable": False, "reason": "fewer than two CPU levels saturated"}

    mem_levels = sorted(obs["memory_mib"].unique())
    ref_mem = max(mem_levels)  # largest limit: no GC pressure

    def design_matrix(frame: pd.DataFrame) -> tuple[np.ndarray, list[str]]:
        cols = [np.ones(len(frame)), np.log(frame["cpu_cores"].to_numpy(float))]
        names = ["log_mu0", "alpha"]
        for m in mem_levels:
            if m == ref_mem:
                continue
            cols.append((frame["memory_mib"] == m).to_numpy(float))
            names.append(f"mem_{m}Mi")
        return np.column_stack(cols), names

    X, names = design_matrix(obs)
    y = np.log(obs["capacity_rps"].to_numpy(float))
    full = _ols(X, y, names)

    alpha = full.get("alpha")
    se = full.stderr("alpha")
    t_stat = (alpha - 1.0) / se if se > 0 else math.inf
    p_value = float(2.0 * stats.t.sf(abs(t_stat), full.dof))
    ci_low, ci_high = full.ci("alpha")

    # Cluster-robust check: resample whole (CPU, memory, replica) cells.
    rng = np.random.default_rng(seed)
    groups = obs.groupby(["cpu_cores", "memory_mib", "checkout_replicas"]).indices
    keys = list(groups)
    boot: list[float] = []
    for _ in range(n_boot):
        pick = rng.integers(0, len(keys), len(keys))
        idx = np.concatenate([groups[keys[i]] for i in pick])
        sample = obs.iloc[idx]
        if sample["cpu_cores"].nunique() < 2:
            continue
        Xb, nb = design_matrix(sample)
        boot.append(_ols(Xb, np.log(sample["capacity_rps"].to_numpy(float)), nb).get("alpha"))
    boot_ci = [float(np.percentile(boot, 2.5)), float(np.percentile(boot, 97.5))] if boot else None

    # Reference-memory-only fit (no GC confound).
    ref = obs[obs["memory_mib"] == ref_mem]
    ref_fit = None
    if ref["cpu_cores"].nunique() >= 2:
        Xr = np.column_stack([np.ones(len(ref)), np.log(ref["cpu_cores"].to_numpy(float))])
        rf = _ols(Xr, np.log(ref["capacity_rps"].to_numpy(float)), ["log_mu0", "alpha"])
        ref_fit = {
            "alpha": round(rf.get("alpha"), 4),
            "ci95": [round(v, 4) for v in rf.ci("alpha")],
            "mu0_rps_at_1_core": round(math.exp(rf.get("log_mu0")), 2),
            "n": len(ref),
        }

    # Misspecification check: local slopes between adjacent CPU levels.
    level_means = (
        ref.groupby("cpu_cores")["capacity_rps"].mean()
        if not ref.empty
        else obs.groupby("cpu_cores")["capacity_rps"].mean()
    )
    cpu_sorted = sorted(level_means.index)
    local_slopes = [
        {
            "from_cores": a,
            "to_cores": b,
            "slope": round(math.log(level_means[b] / level_means[a]) / math.log(b / a), 4),
        }
        for a, b in zip(cpu_sorted, cpu_sorted[1:], strict=False)
    ]

    cpu_levels = sorted(obs["cpu_cores"].unique())
    return {
        "estimable": True,
        "model": "log(mu) = log(mu_0) + alpha*log(cpu_cores) + memory offsets",
        "n_runs": len(obs),
        "alpha": round(alpha, 4),
        "alpha_se": round(se, 4),
        "alpha_ci95": [round(ci_low, 4), round(ci_high, 4)],
        "alpha_ci95_cluster_bootstrap": [round(v, 4) for v in boot_ci] if boot_ci else None,
        "mu0_rps_at_1_core": round(math.exp(full.get("log_mu0")), 2),
        "memory_offsets_log": {
            n: {"coef": round(full.get(n), 4), "ci95": [round(v, 4) for v in full.ci(n)]}
            for n in names
            if n.startswith("mem_")
        },
        "r2": round(full.r2, 4),
        "linear_scaling_test": {
            "h0": "alpha = 1",
            "t_stat": round(t_stat, 2),
            "dof": full.dof,
            "p_value": p_value,
            "rejected_at_5pct": bool(p_value < 0.05),
        },
        "reference_memory_fit": ref_fit,
        "local_slopes": local_slopes,
        "capacity_by_cpu": {
            str(c): round(float(obs.loc[obs["cpu_cores"] == c, "capacity_rps"].mean()), 2)
            for c in cpu_levels
        },
        "generating_model": {
            "form": "mu(s) = 1 / (T_SERIAL + T_PARALLEL / s)",
            "T_SERIAL_s": T_SERIAL,
            "T_PARALLEL_core_s": round(T_PARALLEL, 6),
            "true_capacity_by_cpu": {str(c): round(cpu_capacity(c), 2) for c in cpu_levels},
            "true_least_squares_alpha": round(generating_alpha(cpu_levels), 4),
        },
    }


def plot_capacity_vs_cpu(obs: pd.DataFrame, fit: dict[str, Any], path: Path) -> Path | None:
    """Capacity per replica vs CPU limit (log-log), fitted power law and linear reference."""
    if obs.empty or not fit.get("estimable"):
        return None
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    ink, ink2, grid, surface = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"
    series = {512: ("#2a78d6", "o"), 1024: ("#eb6834", "s"), 2048: ("#1baf7a", "^")}

    fig, ax = plt.subplots(figsize=(7.2, 4.6), dpi=150)
    fig.patch.set_facecolor(surface)
    ax.set_facecolor(surface)
    offsets = {512: -0.035, 1024: 0.0, 2048: 0.035}
    for mem, (colour, marker) in series.items():
        sub = obs[obs["memory_mib"] == mem]
        if sub.empty:
            continue
        jitter = np.exp(offsets[mem])
        ax.scatter(
            sub["cpu_cores"] * jitter,
            sub["capacity_rps"],
            s=36,
            color=colour,
            marker=marker,
            edgecolor=surface,
            linewidth=1.5,
            label=f"measured, {_fmt_mem(mem)} memory",
            zorder=3,
        )

    cpu_grid = np.geomspace(min(obs["cpu_cores"]) * 0.9, max(obs["cpu_cores"]) * 1.1, 100)
    ref = fit.get("reference_memory_fit") or {}
    alpha = ref.get("alpha", fit["alpha"])
    mu0 = ref.get("mu0_rps_at_1_core", fit["mu0_rps_at_1_core"])
    ax.plot(
        cpu_grid,
        mu0 * cpu_grid**alpha,
        color=ink,
        linewidth=2,
        label=f"fit: {mu0:.0f}·s^{alpha:.2f}",
        zorder=2,
    )
    base = mu0 * BASELINE_CPU_CORES**alpha
    ax.plot(
        cpu_grid,
        base * (cpu_grid / BASELINE_CPU_CORES),
        color=ink2,
        linewidth=1.5,
        linestyle=(0, (4, 3)),
        label="linear scaling (α = 1) from 0.5 core",
        zorder=1,
    )
    ax.set_xscale("log")
    ax.set_yscale("log")
    ticks = sorted(obs["cpu_cores"].unique())
    ax.set_xticks(ticks)
    ax.set_xticklabels([f"{t:g}" for t in ticks])
    ax.set_yticks([50, 100, 200, 300])
    ax.set_yticklabels(["50", "100", "200", "300"])
    ax.minorticks_off()
    ax.set_xlabel("CPU limit per replica (cores, log scale)", color=ink2)
    ax.set_ylabel("Saturation throughput per replica (rps, log)", color=ink2)
    lo, hi = fit["alpha_ci95"]
    lin = fit["linear_scaling_test"]
    ax.set_title(
        f"Per-replica capacity vs CPU limit: α = {fit['alpha']:.3f} [{lo:.3f}, {hi:.3f}], "
        f"α=1 {'rejected' if lin['rejected_at_5pct'] else 'not rejected'} (p={lin['p_value']:.1g})",
        color=ink,
        fontsize=9.5,
        loc="left",
    )
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    for spine in ("left", "bottom"):
        ax.spines[spine].set_color(grid)
    ax.tick_params(colors=ink2)
    ax.grid(True, color=grid, linewidth=0.8, zorder=0)
    legend = ax.legend(frameon=False, fontsize=8, loc="upper left")
    for text in legend.get_texts():
        text.set_color(ink)
    fig.text(
        0.01,
        0.01,
        "Simulated (Amdahl-type service model). One point per saturated run; memory levels offset "
        "horizontally for legibility.",
        fontsize=6.5,
        color=ink2,
    )
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, facecolor=surface)
    plt.close(fig)
    return path


# ---------------------------------------------------------------------------
# Mechanism constraints learned from static data
# ---------------------------------------------------------------------------


def learn_mechanism_constraints(
    static_df: pd.DataFrame,
) -> list[tuple[str, str, Constraint, BoundEstimate]]:
    """Learns explicit CPU- and DB-saturation thresholds from the static (500m) data.

    The learned rule set contains no rule that isolates either mechanism, so these
    two are fitted here with the same F1-optimal threshold estimator used for
    boundary shifts:

    * K02 CPU saturation: ``rps_per_replica > b``, fitted on cache-on flash_sale rows
      (DB capacity there is >= 2.2x higher, so the frontend binds first).
    * K03 DB saturation: ``rps_per_pool_conn > b and cache_enabled <= 0.5``, fitted
      on cache-off rows whose frontend CPU is below 90% (so the CPU is not binding).
    """
    df = add_derived_columns(static_df)
    df = df[df["workload_profile"] != "slow_payment_api"]  # different per-replica capacity
    train = df[df["split"] == "train"] if "split" in df.columns else df
    out: list[tuple[str, str, Constraint, BoundEstimate]] = []

    def _make(expr: str, variables: list[str]) -> Constraint:
        return Constraint(
            variables=variables,
            inequality=expr,
            confidence=1.0,
            support=0.0,
            support_count=0,
            source_experiments=[],
            z3_expr=expr,
            method="mechanism_threshold",
            evaluator=_compile_evaluator(expr),
        )

    # Only the flash_sale mix can breach the p95 SLO with the cache on (other mixes cap
    # below 500 ms in the queuing model), and it is the mix the vertical study uses.
    cpu_rows = train[(train["cache_enabled"] == 1) & (train["workload_profile"] == "flash_sale")]
    cpu_bound = estimate_bound(_make("rps_per_replica > 0.0", ["rps_per_replica"]), cpu_rows)
    if cpu_bound.estimable:
        k02 = _make(
            f"rps_per_replica > {cpu_bound.new_bound:.2f}", ["target_rps", "checkout_replicas"]
        )
        out.append(("K02", "mechanism", k02, cpu_bound))

    db_rows = train[(train["cache_enabled"] == 0) & (train["cpu_pct"] < 90.0)]
    db_bound = estimate_bound(
        _make("rps_per_pool_conn > 0.0 and cache_enabled <= 0.50", ["rps_per_pool_conn"]), db_rows
    )
    if db_bound.estimable:
        k03 = _make(
            f"rps_per_pool_conn > {db_bound.new_bound:.2f} and cache_enabled <= 0.50",
            ["target_rps", "db_pool_size", "cache_enabled"],
        )
        out.append(("K03", "mechanism", k03, db_bound))

    heldout = df[df["split"] == "held_out"] if "split" in df.columns else df
    for _, _, constraint, _ in out:
        metrics = evaluate_constraint(constraint, heldout, "all")
        constraint.heldout_precision = metrics.precision
        constraint.heldout_recall = metrics.recall
        constraint.heldout_support = metrics.support
    return out


# ---------------------------------------------------------------------------
# 2 + 5. Classification with boundary probing before "disappeared"
# ---------------------------------------------------------------------------


@dataclass
class VerticalTransfer:
    constraint_id: str
    source: str
    rule: str
    variables: list[str]
    classification: str
    steady: PhaseMetrics
    transition: PhaseMetrics
    baseline_precision: float | None
    bound: BoundEstimate | None
    bounds_by_cpu: dict[str, dict[str, Any]]
    class_by_cpu: dict[str, str]
    evidence: str
    probe: dict[str, Any] | None = None
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "constraint_id": self.constraint_id,
            "source": self.source,
            "rule": self.rule,
            "variables": self.variables,
            "classification": self.classification,
            "baseline_static_precision": self.baseline_precision,
            "steady": self.steady.to_dict(),
            "transition": self.transition.to_dict(),
            "bound": self.bound.to_dict() if self.bound else None,
            "bounds_by_cpu": self.bounds_by_cpu,
            "class_by_cpu": self.class_by_cpu,
            "evidence": self.evidence,
            "boundary_probe": self.probe,
            "notes": self.notes,
        }


def _rule_distance(
    constraint: Constraint, df: pd.DataFrame, ranges: dict[str, float]
) -> np.ndarray:
    """Normalised distance of each row from satisfying *constraint* (0 = inside)."""
    linear = _linear_spec(constraint.inequality)
    if linear is not None:
        coefs, bound = linear
        score = np.zeros(len(df))
        for var, coef in coefs.items():
            score += coef * df[var].to_numpy(float)
        return np.maximum(0.0, bound - score) / max(1e-9, abs(bound))
    dist = np.zeros(len(df))
    for var, op, raw in _SIMPLE_CLAUSE.findall(constraint.inequality):
        if var not in df.columns:
            continue
        x = df[var].to_numpy(float)
        b = float(raw)
        gap = np.maximum(0.0, b - x) if op in (">", ">=") else np.maximum(0.0, x - b)
        dist += gap / max(1e-9, ranges.get(var, 1.0))
    return dist


def _critical_gap(constraint: Constraint, df: pd.DataFrame) -> np.ndarray:
    """How far each row sits from the rule's critical boundary (smaller = closer)."""
    linear = _linear_spec(constraint.inequality)
    if linear is not None:
        coefs, bound = linear
        score = np.zeros(len(df))
        for var, coef in coefs.items():
            score += coef * df[var].to_numpy(float)
        return np.abs(score - bound) / max(1e-9, abs(bound))
    clauses = [c for c in _SIMPLE_CLAUSE.findall(constraint.inequality) if c[0] in df.columns]
    increasing = [c for c in clauses if c[1] in (">", ">=") and c[0] != "cache_enabled"]
    if not increasing:
        return np.zeros(len(df))
    var, _, raw = increasing[0]
    b = float(raw)
    return np.abs(df[var].to_numpy(float) - b) / max(1e-9, abs(b))


def probe_near_boundary(
    constraint: Constraint,
    design: VerticalStudyDesign,
    n_probes: int = 6,
    seed: int = 7,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Runs extra experiments as close to the rule's old boundary as the region allows.

    Candidates densify offered load around every numeric threshold in the rule and
    span all CPU/memory/replica/pool/cache levels. Points inside the rule's region
    and nearest its critical boundary are preferred, spread across CPU levels.
    """
    clauses = _SIMPLE_CLAUSE.findall(constraint.inequality)
    rps_bounds = [float(v) for var, _, v in clauses if var == "target_rps"]
    linear = _linear_spec(constraint.inequality)
    rps_candidates = set(design.rps_levels)
    for b in rps_bounds:
        rps_candidates.update(int(round(b * f)) for f in (0.96, 0.99, 1.01, 1.02, 1.04, 1.08))
    if linear is not None:
        coefs, bound = linear
        a = coefs.get("target_rps", 0.0)
        if a > 0:
            for rep, pool, cache in itertools.product(
                design.replicas, design.db_pools, design.cache
            ):
                rest = sum(
                    coefs.get(v, 0.0) * x
                    for v, x in (
                        ("checkout_replicas", rep),
                        ("db_pool_size", pool),
                        ("cache_enabled", int(cache)),
                    )
                )
                r_star = (bound - rest) / a
                if 50 <= r_star <= 1500:
                    rps_candidates.update(int(round(r_star * f)) for f in (1.01, 1.03))

    cand = pd.DataFrame(
        [
            {
                "cpu_cores": cpu,
                "memory_mib": mem,
                "checkout_replicas": rep,
                "db_pool_size": pool,
                "cache_enabled": int(cache),
                "target_rps": float(rps),
            }
            for cpu, mem, rep, pool, cache, rps in itertools.product(
                design.cpu_cores,
                design.memory_mib,
                design.replicas,
                design.db_pools,
                design.cache,
                sorted(rps_candidates),
            )
        ]
    )
    cand = add_derived_columns(cand)
    ranges = {
        "target_rps": float(max(design.rps_levels) - min(design.rps_levels)),
        "checkout_replicas": float(max(design.replicas) - min(design.replicas)) or 1.0,
        "db_pool_size": float(max(design.db_pools) - min(design.db_pools)) or 1.0,
        "cache_enabled": 1.0,
    }
    cand["distance"] = _rule_distance(constraint, cand, ranges)
    cand["gap"] = _critical_gap(constraint, cand)
    cand = cand.sort_values(["distance", "gap"]).reset_index(drop=True)

    # Spread probes across CPU levels so a CPU-dependent boundary is visible.
    chosen: list[int] = []
    for i in itertools.cycle(range(len(design.cpu_cores))):
        if len(chosen) >= n_probes:
            break
        cpu = design.cpu_cores[i]
        pool_idx = [j for j in cand.index[cand["cpu_cores"] == cpu] if j not in chosen]
        if not pool_idx:
            if all(j in chosen for j in cand.index):
                break
            continue
        chosen.append(pool_idx[0])

    frames = []
    for k, idx in enumerate(chosen):
        row = cand.loc[idx]
        cell = {
            "cpu_cores": float(row["cpu_cores"]),
            "memory_mib": int(row["memory_mib"]),
            "checkout_replicas": int(row["checkout_replicas"]),
            "db_pool_size": int(row["db_pool_size"]),
            "cache_enabled": bool(row["cache_enabled"]),
            "target_rps": float(row["target_rps"]),
        }
        _, frame = simulate_vertical_run(cell, design, repeat=500 + k, seed=seed + k)
        frame["split"] = "probe"
        frames.append(frame)
    probe_rows = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    closest = cand.loc[chosen[0]] if chosen else None
    info = {
        "experiments": len(chosen),
        "closest_point": (
            {
                "cpu_cores": float(closest["cpu_cores"]),
                "memory_mib": int(closest["memory_mib"]),
                "checkout_replicas": int(closest["checkout_replicas"]),
                "db_pool_size": int(closest["db_pool_size"]),
                "cache_enabled": bool(closest["cache_enabled"]),
                "target_rps": float(closest["target_rps"]),
                "normalised_distance_to_rule_region": round(float(closest["distance"]), 4),
                "relative_gap_to_boundary": round(float(closest["gap"]), 4),
            }
            if closest is not None
            else None
        ),
        "probe_configs": [
            {
                k: (float(v) if isinstance(v, (np.floating, float)) else int(v))
                for k, v in cand.loc[
                    i,
                    [
                        "cpu_cores",
                        "memory_mib",
                        "checkout_replicas",
                        "db_pool_size",
                        "cache_enabled",
                        "target_rps",
                    ],
                ].items()
            }
            for i in chosen
        ],
    }
    return probe_rows, info


def _bound_dict(bound: BoundEstimate | None) -> dict[str, Any] | None:
    return bound.to_dict() if bound is not None else None


def classify_vertical(
    constraint_id: str,
    source: str,
    constraint: Constraint,
    rows_train: pd.DataFrame,
    rows_heldout: pd.DataFrame,
    design: VerticalStudyDesign,
    bound_rows_filter: str | None = None,
    probe: bool = True,
) -> tuple[VerticalTransfer, pd.DataFrame]:
    """Classifies one constraint, probing near its boundary before 'disappeared'."""
    steady_train = rows_train[rows_train["phase"] == "steady"]
    if bound_rows_filter:
        steady_train = steady_train.query(bound_rows_filter)

    def _evaluate(heldout: pd.DataFrame, train_steady: pd.DataFrame):
        steady = evaluate_constraint(constraint, heldout, "steady")
        transition = evaluate_constraint(constraint, heldout, "transition")
        bound = estimate_bound(constraint, train_steady) if source != "structural" else None
        cls, ev = _classify(
            source=source if source != "mechanism" else "learned",
            steady=steady,
            transition=transition,
            baseline_precision=constraint.heldout_precision,
            bound=bound,
            connection_violations=0,
        )
        return steady, transition, bound, cls, ev

    steady, transition, bound, cls, evidence = _evaluate(rows_heldout, steady_train)

    # Per-CPU-level view (questions 2 and 3).
    bounds_by_cpu: dict[str, dict[str, Any]] = {}
    class_by_cpu: dict[str, str] = {}
    for cpu in design.cpu_cores:
        tr = steady_train[steady_train["cpu_cores"] == cpu]
        ho = rows_heldout[rows_heldout["cpu_cores"] == cpu]
        b = estimate_bound(constraint, tr) if source != "structural" else None
        st = evaluate_constraint(constraint, ho, "steady")
        tn = evaluate_constraint(constraint, ho, "transition")
        c, _ = _classify(
            source="learned",
            steady=st,
            transition=tn,
            baseline_precision=constraint.heldout_precision,
            bound=b,
            connection_violations=0,
        )
        class_by_cpu[f"{cpu:g}"] = c
        bounds_by_cpu[f"{cpu:g}"] = {
            "bound": _bound_dict(b),
            "steady_precision": round(st.precision, 4),
            "steady_recall": round(st.recall, 4),
            "steady_support": st.support,
        }

    probe_info = None
    probe_rows = pd.DataFrame()
    if probe and cls == CLASS_DISAPPEARED:
        probe_rows, probe_info = probe_near_boundary(constraint, design)
        if not probe_rows.empty:
            heldout_plus = pd.concat([rows_heldout, probe_rows], ignore_index=True)
            train_plus = pd.concat(
                [steady_train, probe_rows[probe_rows["phase"] == "steady"]], ignore_index=True
            )
            steady, transition, bound, cls, evidence = _evaluate(heldout_plus, train_plus)
            probe_metrics = evaluate_constraint(constraint, probe_rows, "all")
            probe_info["probe_support"] = probe_metrics.support
            probe_info["probe_precision"] = round(probe_metrics.precision, 4)
            probe_info["probe_slo_violation_rate"] = round(
                float(probe_rows["slo_violated"].mean()), 4
            )
            probe_info["class_after_probe"] = cls

    notes: list[str] = []
    if cls == CLASS_DISAPPEARED and probe_info:
        cp = probe_info.get("closest_point") or {}
        notes.append(
            f"Confirmed after {probe_info['experiments']} boundary probes; closest point tested: "
            f"{cp.get('target_rps')} rps, {cp.get('checkout_replicas')} replicas, pool "
            f"{cp.get('db_pool_size')}, cache {'on' if cp.get('cache_enabled') else 'off'}, "
            f"{cp.get('cpu_cores')} cores / {cp.get('memory_mib')}Mi "
            f"(distance to rule region {cp.get('normalised_distance_to_rule_region')}, "
            f"gap to boundary {cp.get('relative_gap_to_boundary')})."
        )
        notes.append(f"Tested region: {json.dumps(vertical_tested_region(design))}.")
    distinct = sorted(set(class_by_cpu.values()))
    if len(distinct) > 1:
        notes.append(
            "Class depends on CPU limit: "
            + ", ".join(f"{k} cores={v}" for k, v in class_by_cpu.items())
            + "."
        )

    return (
        VerticalTransfer(
            constraint_id=constraint_id,
            source=source,
            rule=constraint.inequality,
            variables=list(constraint.variables),
            classification=cls,
            steady=steady,
            transition=transition,
            baseline_precision=constraint.heldout_precision,
            bound=bound,
            bounds_by_cpu=bounds_by_cpu,
            class_by_cpu=class_by_cpu,
            evidence=evidence,
            probe=probe_info,
            notes=" ".join(notes),
        ),
        probe_rows,
    )


# ---------------------------------------------------------------------------
# New vertical constraints
# ---------------------------------------------------------------------------


def discover_vertical_constraints(
    rows_train: pd.DataFrame,
    rows_heldout: pd.DataFrame,
    validated: list[tuple[str, str, Constraint]],
    alpha: float | None,
    min_precision: float = 0.8,
    min_support: int = 30,
) -> list[Constraint]:
    """Learns rules that need the resource limits to express.

    * A CPU-normalised saturation rule ``rps_per_replica / cpu_cores**alpha > b``
      (alpha from the capacity fit): the static CPU rule generalised to any limit.
    * Decision-tree rules over the vertical features, kept only when they
      reference ``cpu_cores`` or ``memory_mib`` and do not duplicate a validated rule.
    """
    found: list[Constraint] = []
    steady_train = rows_train[rows_train["phase"] == "steady"]
    steady_heldout = rows_heldout[rows_heldout["phase"] == "steady"]

    if alpha is not None:
        col = f"rps_per_replica / cpu_cores ** {alpha:.3f}"
        tmp = steady_train.assign(rps_per_scaled_core=steady_train.eval(col))
        tmp = tmp[tmp["cache_enabled"] == 1]
        seed_rule = Constraint(
            variables=["rps_per_scaled_core"],
            inequality="rps_per_scaled_core > 0.0",
            confidence=1.0,
            support=0.0,
            support_count=0,
            source_experiments=[],
            z3_expr="",
            method="mechanism_threshold",
            evaluator=None,
        )
        b = estimate_bound(seed_rule, tmp)
        if b.estimable:
            expr = (
                f"target_rps / (checkout_replicas * cpu_cores ** {alpha:.3f}) > {b.new_bound:.2f}"
            )
            rule = Constraint(
                variables=["target_rps", "checkout_replicas", "cpu_cores"],
                inequality=expr,
                confidence=b.precision,
                support=0.0,
                support_count=b.rows_used,
                source_experiments=[],
                z3_expr=expr,
                method="mechanism_threshold",
                evaluator=_compile_evaluator(expr),
            )
            m = evaluate_constraint(
                rule, steady_heldout[steady_heldout["cache_enabled"] == 1], "all"
            )
            rule.heldout_precision, rule.heldout_recall = m.precision, m.recall
            rule.heldout_f1, rule.heldout_support = m.f1, m.support
            found.append(rule)

    features = [
        "target_rps",
        "checkout_replicas",
        "db_pool_size",
        "cache_enabled",
        "cpu_cores",
        "memory_mib",
    ]
    candidates = learn_decision_tree_constraints(
        df_train=steady_train,
        features=features,
        max_depths=[2, 3],
        min_samples_leaf=20,
        min_confidence=0.85,
    )
    all_rows = pd.concat([rows_train, rows_heldout], ignore_index=True)
    validated_masks = [np.asarray(c.matches(all_rows), dtype=bool) for _, _, c in validated]
    for cand in candidates:
        if not ({"cpu_cores", "memory_mib"} & set(cand.variables)):
            continue
        mask = np.asarray(cand.matches(all_rows), dtype=bool)
        if any(_mask_overlap(mask, m) >= 0.9 for m in validated_masks):
            continue
        m = evaluate_constraint(cand, steady_heldout, "all")
        if m.support < min_support or m.precision < min_precision:
            continue
        cand.heldout_precision, cand.heldout_recall = m.precision, m.recall
        cand.heldout_f1, cand.heldout_support = m.f1, m.support
        found.append(cand)
    # Keep the most specific few (highest held-out F1).
    tree_rules = sorted(
        found[1:] if alpha is not None else found, key=lambda c: -(c.heldout_f1 or 0)
    )
    head = found[:1] if alpha is not None else []
    return head + tree_rules[:3]


# ---------------------------------------------------------------------------
# 3. Binding analysis and 4. connections per replica
# ---------------------------------------------------------------------------


def binding_analysis(rows: pd.DataFrame) -> list[dict[str, Any]]:
    """Which resource is binding when the SLO is violated, per CPU limit.

    A steady violating second is CPU-bound when frontend CPU is >= 93% of limit
    and DB-bound otherwise (offered load exceeds the pool's capacity while the
    frontend has headroom).
    """
    steady = rows[rows["phase"] == "steady"]
    out = []
    for cpu, grp in steady.groupby("cpu_cores"):
        viol = grp[grp["slo_violated"]]
        cpu_bound = int((viol["cpu_utilization_pct"] >= 93.0).sum())
        db_bound = int(len(viol) - cpu_bound)
        out.append(
            {
                "cpu_cores": float(cpu),
                "steady_rows": len(grp),
                "slo_violations": len(viol),
                "cpu_bound_violations": cpu_bound,
                "db_bound_violations": db_bound,
                "cpu_bound_share": round(cpu_bound / len(viol), 4) if len(viol) else 0.0,
                "mean_throttled_pct_in_violations": (
                    round(float(viol["throttled_periods_pct"].mean()), 2) if len(viol) else 0.0
                ),
            }
        )
    return out


def connections_per_replica_analysis(rows: pd.DataFrame) -> dict[str, Any]:
    """Tests whether DB connections per replica depend on CPU or memory limits.

    Two views:
    * *Matched load*: OLS of connections-per-replica on CPU and memory level
      dummies with fixed effects for offered load, replicas, pool and cache. A
      non-zero CPU coefficient means the same load uses a different number of
      connections per pod.
    * *Per unit of utilisation*: the same model with frontend utilisation added.
      If CPU/memory coefficients vanish, the per-replica connection demand curve
      itself is unchanged and the matched-load effect is mediated by utilisation.
    """
    df = rows[(rows["phase"] == "steady") & (rows["ready_replicas"] > 0)].copy()
    if df.empty:
        return {"estimable": False}
    df["utilisation"] = (df["rps_per_replica"] / df["cpu_cores"].map(cpu_capacity)).clip(upper=1.2)

    def dummies(col: str, ref: Any) -> tuple[list[np.ndarray], list[str]]:
        cols, names = [], []
        for level in sorted(df[col].unique()):
            if level == ref:
                continue
            cols.append((df[col] == level).to_numpy(float))
            names.append(f"{col}={level:g}")
        return cols, names

    def fit(with_util: bool) -> dict[str, Any]:
        cols, names = [np.ones(len(df))], ["intercept"]
        for col, ref in (
            ("cpu_cores", BASELINE_CPU_CORES),
            ("memory_mib", 512),
            ("target_rps", min(df["target_rps"])),
            ("checkout_replicas", min(df["checkout_replicas"])),
            ("db_pool_size", min(df["db_pool_size"])),
            ("cache_enabled", 0),
        ):
            c, n = dummies(col, ref)
            cols += c
            names += n
        if with_util:
            cols.append(df["utilisation"].to_numpy(float))
            names.append("utilisation")
        res = _ols(np.column_stack(cols), df["db_conns_per_replica"].to_numpy(float), names)
        keep = [n for n in names if n.startswith(("cpu_cores", "memory_mib")) or n == "utilisation"]
        return {
            n: {
                "coef": round(res.get(n), 4),
                "ci95": [round(v, 4) for v in res.ci(n)],
                "significant": bool(not (res.ci(n)[0] <= 0.0 <= res.ci(n)[1])),
            }
            for n in keep
        } | {"r2": round(res.r2, 4), "n": len(df)}

    saturated = df[df["utilisation"] >= 1.0]
    peak = (
        saturated.groupby(["cpu_cores", "memory_mib"])["db_conns_per_replica"]
        .agg(["mean", "max", "count"])
        .round(3)
        .reset_index()
        .to_dict(orient="records")
    )
    by_cpu_matched = (
        df[(df["target_rps"] == 350) & (df["checkout_replicas"] == 4)]
        .groupby("cpu_cores")["db_conns_per_replica"]
        .mean()
        .round(3)
        .to_dict()
    )
    return {
        "estimable": True,
        "matched_load_model": fit(with_util=False),
        "utilisation_adjusted_model": fit(with_util=True),
        "saturated_conns_per_replica": peak,
        "example_350rps_4replicas_mean_conns_per_replica_by_cpu": {
            f"{k:g}": v for k, v in by_cpu_matched.items()
        },
    }


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


@dataclass
class VerticalStudyResult:
    design: VerticalStudyDesign
    runs: pd.DataFrame
    rows: pd.DataFrame
    capacity_obs: pd.DataFrame
    capacity_fit: dict[str, Any]
    transfers: list[VerticalTransfer]
    new_constraints: list[Constraint]
    mechanism_static_bounds: dict[str, dict[str, Any]]
    binding: list[dict[str, Any]]
    connections: dict[str, Any]
    restarts: dict[str, Any]
    plot_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": "14D",
            "model_note": (
                "All runs are simulated: queuing model from simulate_system_metrics with a "
                "per-request serial+parallel CPU service model, a working-set memory model with "
                "OOM restarts, and a CFS throttling model. Not a hardware measurement."
            ),
            "design": {
                "cells": len(self.design.cells()),
                "runs": len(self.runs),
                "rows": len(self.rows),
                "repeats": self.design.repeats,
                "heldout_cells": len(self.design.split_cells()[1]),
            },
            "tested_region": vertical_tested_region(self.design),
            "capacity_fit": self.capacity_fit,
            "constraints": [t.to_dict() for t in self.transfers],
            "new_constraints": [c.to_dict() for c in self.new_constraints],
            "mechanism_static_bounds": self.mechanism_static_bounds,
            "binding_analysis": self.binding,
            "connections_per_replica": self.connections,
            "pod_restarts": self.restarts,
            "plot": self.plot_path,
        }


def _restart_summary(runs: pd.DataFrame) -> dict[str, Any]:
    grp = (
        runs.groupby(["memory_mib", "cpu_cores"])
        .agg(runs=("run_id", "count"), restarts=("pod_restarts", "sum"))
        .reset_index()
    )
    grp["runs_with_restarts"] = (
        runs.assign(r=runs["pod_restarts"] > 0)
        .groupby(["memory_mib", "cpu_cores"])["r"]
        .sum()
        .to_numpy()
    )
    throttle = runs.groupby("cpu_cores")["mean_throttled_pct"].mean().round(2).to_dict()
    return {
        "total_restarts": int(runs["pod_restarts"].sum()),
        "by_memory_and_cpu": grp.to_dict(orient="records"),
        "mean_throttled_pct_by_cpu": {f"{k:g}": v for k, v in throttle.items()},
    }


def run_vertical_study(
    design: VerticalStudyDesign | None = None,
    constraints_path: str | Path = DEFAULT_CONSTRAINTS_PATH,
    static_path: str | Path = DEFAULT_STATIC_PATH,
    plot_path: str | Path | None = BASE_DIR / "data" / "vertical_capacity_vs_cpu.png",
    probe: bool = True,
) -> VerticalStudyResult:
    design = design or VerticalStudyDesign()
    validated = load_validated_constraints(constraints_path, include_connection_invariant=False)

    static_df = pd.read_parquet(static_path)
    mechanism = learn_mechanism_constraints(static_df)
    mechanism_bounds = {cid: b.to_dict() for cid, _, _, b in mechanism}

    runs, rows = build_vertical_dataset(design)
    rows_train = rows[rows["split"] == "train"]
    rows_heldout = rows[rows["split"] == "held_out"]
    logger.info("Vertical study simulated: %d runs, %d rows", len(runs), len(rows))

    obs = capacity_observations(rows)
    fit = fit_capacity_power_law(obs)
    saved_plot = None
    if plot_path is not None:
        p = plot_capacity_vs_cpu(obs, fit, Path(plot_path))
        saved_plot = str(p) if p else None

    transfers: list[VerticalTransfer] = []
    probe_frames: list[pd.DataFrame] = []
    for cid, source, constraint in validated:
        rec, probes = classify_vertical(
            cid, source, constraint, rows_train, rows_heldout, design, probe=probe
        )
        transfers.append(rec)
        if not probes.empty:
            probe_frames.append(probes)
    for cid, _, constraint, _ in mechanism:
        # Each mechanism threshold is re-estimated where the *other* resource is not binding.
        flt = "cache_enabled == 1" if cid == "K02" else "cpu_utilization_pct < 90"
        rec, probes = classify_vertical(
            cid,
            "mechanism",
            constraint,
            rows_train,
            rows_heldout,
            design,
            bound_rows_filter=flt,
            probe=probe,
        )
        transfers.append(rec)
        if not probes.empty:
            probe_frames.append(probes)

    alpha = fit.get("alpha") if fit.get("estimable") else None
    new_rules = discover_vertical_constraints(
        rows_train, rows_heldout, validated + [(c, s, k) for c, s, k, _ in mechanism], alpha
    )

    all_rows = pd.concat([rows, *probe_frames], ignore_index=True) if probe_frames else rows
    return VerticalStudyResult(
        design=design,
        runs=runs,
        rows=all_rows,
        capacity_obs=obs,
        capacity_fit=fit,
        transfers=transfers,
        new_constraints=new_rules,
        mechanism_static_bounds=mechanism_bounds,
        binding=binding_analysis(rows),
        connections=connections_per_replica_analysis(rows),
        restarts=_restart_summary(runs),
        plot_path=saved_plot,
    )


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _fmt_bound(bound: BoundEstimate | None) -> str:
    if bound is None:
        return "n/a"
    if not bound.estimable or bound.new_bound is None:
        return f"{bound.old_bound:.3g} -> n/a ({bound.note})"
    return (
        f"{bound.old_bound:.3g} -> {bound.new_bound:.3g} [{bound.ci_low:.3g}, {bound.ci_high:.3g}]"
    )


def _fmt_cpu_bounds(rec: VerticalTransfer) -> str:
    parts = []
    for cpu, payload in rec.bounds_by_cpu.items():
        b = payload.get("bound") or {}
        nb = b.get("new_bound")
        parts.append(f"{cpu}c: {nb:.3g}" if nb is not None else f"{cpu}c: n/a")
    return "; ".join(parts)


def format_vertical_table(result: VerticalStudyResult) -> str:
    lines = [
        "| ID | Constraint (condition => SLO violation) | Class | Steady P/R (n) | "
        "Old -> pooled bound [95% CI] | Bound by CPU limit | Class by CPU | Evidence / notes |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for rec in result.transfers:
        ev = rec.evidence
        if rec.notes:
            ev = f"{ev}{' ' if ev.endswith('.') else '. '}{rec.notes}"
        cls_cpu = ", ".join(f"{k}c {v}" for k, v in rec.class_by_cpu.items())
        lines.append(
            f"| {rec.constraint_id} | `{rec.rule}` | **{rec.classification}** | "
            f"P {rec.steady.precision:.2f} / R {rec.steady.recall:.2f} (n={rec.steady.support}) | "
            f"{_fmt_bound(rec.bound)} | {_fmt_cpu_bounds(rec)} | {cls_cpu} | {ev} |"
        )
    for i, c in enumerate(result.new_constraints, start=1):
        lines.append(
            f"| V{i:02d} | `{c.inequality}` | **{CLASS_NEW}** | "
            f"P {c.heldout_precision:.2f} / R {c.heldout_recall:.2f} (n={c.heldout_support}) | "
            f"n/a | n/a | n/a | Needs the resource limits to express; held-out steady rows. |"
        )
    return "\n".join(lines)


def format_vertical_report(result: VerticalStudyResult) -> str:
    fit = result.capacity_fit
    out = ["# Task 14D: constraints under vertical scaling", ""]
    out.append(
        "> Simulated study (queuing model + serial/parallel CPU service model + working-set "
        "memory/OOM model + CFS throttling model). Not a hardware measurement."
    )
    out.append("")
    out.append(
        f"Design: {len(result.design.cells())} cells x {result.design.repeats} repeats = "
        f"{len(result.runs)} runs; region: `{json.dumps(vertical_tested_region(result.design))}`"
    )
    out.append("")
    out.append("## 1. Per-replica capacity vs CPU limit")
    out.append("")
    if fit.get("estimable"):
        lin = fit["linear_scaling_test"]
        ref = fit.get("reference_memory_fit") or {}
        gen = fit["generating_model"]
        out.append(
            f"- Fit `mu(s) = mu_0 * s^alpha` on {fit['n_runs']} saturated runs: "
            f"**alpha = {fit['alpha']:.3f}**, 95% CI [{fit['alpha_ci95'][0]:.3f}, "
            f"{fit['alpha_ci95'][1]:.3f}] (cluster bootstrap "
            f"{fit['alpha_ci95_cluster_bootstrap']}), mu_0 = {fit['mu0_rps_at_1_core']:.1f} rps at 1 core, "
            f"R² = {fit['r2']:.3f}."
        )
        out.append(
            f"- Linear scaling (alpha = 1): t = {lin['t_stat']}, p = {lin['p_value']:.2g} -> "
            f"**{'rejected' if lin['rejected_at_5pct'] else 'not rejected'}** at 5%."
        )
        if ref:
            out.append(
                f"- Largest-memory-only fit (no GC pressure): alpha = {ref['alpha']:.3f} "
                f"[{ref['ci95'][0]:.3f}, {ref['ci95'][1]:.3f}], n = {ref['n']}."
            )
        out.append(
            "- Local slopes: "
            + ", ".join(
                f"{s['from_cores']:g}->{s['to_cores']:g} cores: {s['slope']:.3f}"
                for s in fit["local_slopes"]
            )
            + ". A falling slope means a power law is only an approximation: returns diminish "
            "as the serial part dominates."
        )
        out.append(
            "- Memory offsets (log capacity vs largest limit): "
            + ", ".join(
                f"{k}: {v['coef']:+.3f} [{v['ci95'][0]:+.3f}, {v['ci95'][1]:+.3f}]"
                for k, v in fit["memory_offsets_log"].items()
            )
        )
        out.append(
            f"- Check against the generating model: true capacities "
            f"{gen['true_capacity_by_cpu']}, true least-squares alpha "
            f"{gen['true_least_squares_alpha']:.3f}."
        )
    else:
        out.append(f"- Not estimable: {fit.get('reason')}")
    if result.plot_path:
        out.append(f"- Plot: `{Path(result.plot_path).name}`")
    out.append("")
    out.append("## 2. Constraint classification")
    out.append("")
    out.append(format_vertical_table(result))
    out.append("")
    out.append("Static (500m) bounds of the mechanism constraints:")
    for cid, b in result.mechanism_static_bounds.items():
        out.append(
            f"- {cid}: {b['statistic']} > {b['new_bound']} [95% CI {b['ci_low']}, {b['ci_high']}], "
            f"F1 {b['f1']}"
        )
    out.append("")
    out.append("## 3. Does CPU saturation stop binding? Does the DB constraint stay?")
    out.append("")
    out.append(
        "| CPU limit | Steady rows | SLO violations | CPU-bound | DB-bound | CPU-bound share |"
    )
    out.append("|---|---|---|---|---|---|")
    for b in result.binding:
        out.append(
            f"| {b['cpu_cores']:g} | {b['steady_rows']} | {b['slo_violations']} | "
            f"{b['cpu_bound_violations']} | {b['db_bound_violations']} | {b['cpu_bound_share']:.2f} |"
        )
    k02 = next((t for t in result.transfers if t.constraint_id == "K02"), None)
    k03 = next((t for t in result.transfers if t.constraint_id == "K03"), None)
    if k02:
        out.append("")
        out.append(
            f"- K02 (CPU saturation) by CPU limit: {_fmt_cpu_bounds(k02)}; classes {k02.class_by_cpu}."
        )
    if k03:
        out.append(
            f"- K03 (DB saturation) by CPU limit: {_fmt_cpu_bounds(k03)}; classes {k03.class_by_cpu}."
        )
    out.append("")
    out.append("## 4. DB connections per replica vs CPU and memory limits")
    out.append("")
    conn = result.connections
    if conn.get("estimable"):
        for label, key in (
            ("Matched offered load", "matched_load_model"),
            ("Adjusted for frontend utilisation", "utilisation_adjusted_model"),
        ):
            model = conn[key]
            terms = ", ".join(
                f"{k}: {v['coef']:+.3f} [{v['ci95'][0]:+.3f}, {v['ci95'][1]:+.3f}]"
                f"{' *' if v['significant'] else ''}"
                for k, v in model.items()
                if isinstance(v, dict)
            )
            out.append(f"- {label} (n={model['n']}, R²={model['r2']}): {terms}")
        out.append(
            f"- Example, 350 rps on 4 replicas, mean connections per replica by CPU: "
            f"{conn['example_350rps_4replicas_mean_conns_per_replica_by_cpu']}"
        )
        sat = conn["saturated_conns_per_replica"]
        if sat:
            out.append(
                "- At saturation (utilisation >= 1): "
                + ", ".join(
                    f"{r['cpu_cores']:g}c/{int(r['memory_mib'])}Mi mean {r['mean']}, max {r['max']}"
                    for r in sat
                )
            )
    out.append("")
    out.append("## Pod restarts and throttling")
    out.append("")
    rs = result.restarts
    out.append(f"- Total OOM restarts: {rs['total_restarts']}")
    out.append("| Memory | CPU | Runs | Runs with restarts | Restarts |")
    out.append("|---|---|---|---|---|")
    for r in rs["by_memory_and_cpu"]:
        out.append(
            f"| {_fmt_mem(int(r['memory_mib']))} | {r['cpu_cores']:g} | {r['runs']} | "
            f"{r['runs_with_restarts']} | {r['restarts']} |"
        )
    out.append(f"- Mean throttled CFS periods by CPU limit: {rs['mean_throttled_pct_by_cpu']}")
    return "\n".join(out) + "\n"


def save_vertical_outputs(
    result: VerticalStudyResult,
    output_dir: str | Path = BASE_DIR / "data",
    prefix: str = "vertical_transfer",
) -> dict[str, Path]:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = {
        "runs": out / f"{prefix}_runs.parquet",
        "rows": out / f"{prefix}_rows.parquet",
        "capacity": out / f"{prefix}_capacity_obs.csv",
        "summary": out / f"{prefix}_summary.json",
        "report": out / f"{prefix}_report.md",
    }
    runs = result.runs.copy()
    runs["oom_events"] = runs["oom_events"].map(json.dumps)
    runs.to_parquet(paths["runs"], index=False)
    result.rows.to_parquet(paths["rows"], index=False)
    result.capacity_obs.to_csv(paths["capacity"], index=False)
    paths["summary"].write_text(json.dumps(result.to_dict(), indent=2, default=_json_default))
    paths["report"].write_text(format_vertical_report(result))
    return paths
