"""Task 14C: transfer of static-scaling constraints to horizontal autoscaling.

The validated constraint set learned under *static* replica control (Task 12/13)
is held fixed. This module executes a factorial HPA study, labels every second of
each run as ``steady`` or ``transition``, and evaluates each learned constraint
against what actually happened in each phase.

It then answers three questions:

1. Does each rule's prediction still match reality (precision/recall) when the
   replica count is set by HPA rather than by the experimenter?
2. Has the violation boundary moved, and if so by how much (with a bootstrap
   confidence interval)?
3. Can HPA drive the deployment into a configuration that violates the
   structural connection invariant ``replicas * connections_per_replica <= pool``,
   and if so which autoscaler action caused it?

Shifted constraints are re-fitted with the minimum number of extra experiments,
selected by GP posterior uncertainty near the SLO boundary.

Everything is simulated with the same queuing model used elsewhere in the
repository (``scopex.storage.dataset.simulate_system_metrics``). That is stated
explicitly in the output so the result is not mistaken for a hardware measurement.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import logging
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from scopex.discovery.constraints import Constraint, learn_decision_tree_constraints
from scopex.discovery.surrogate import GPSurrogate
from scopex.storage.config import ExperimentConfig, WorkloadSpec
from scopex.storage.dataset import simulate_system_metrics
from workload.profiles import instantaneous_rps_fraction

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DEFAULT_CONSTRAINTS_PATH = BASE_DIR / "data" / "constraints_summary.json"

#: Calibrated per-replica DB connection demand used by ``simulate_system_metrics``.
CONNECTIONS_PER_REPLICA = 6.0

VIOLATION_THRESHOLD_MS = 500.0

CLASS_UNCHANGED = "unchanged"
CLASS_SHIFTED = "shifted"
CLASS_DISAPPEARED = "disappeared"
CLASS_NEW_TRANSIENT = "new (transient)"
CLASS_VIOLATED = "violated-by-autoscaler"

#: A bound must move by at least this fraction of its old magnitude before it is
#: called a shift. Without it, threshold-grid jitter (a few tenths of a percent)
#: would masquerade as a regime change.
MIN_RELATIVE_BOUND_SHIFT = 0.05


def _relative_bound_shift(bound: BoundEstimate) -> float:
    """Relative displacement of the estimated bound from the validated bound."""
    if bound.new_bound is None:
        return 0.0
    scale = abs(bound.old_bound)
    if scale < 1e-9:
        return math.inf if abs(bound.new_bound) > 1e-9 else 0.0
    return abs(bound.new_bound - bound.old_bound) / scale


# ---------------------------------------------------------------------------
# Design
# ---------------------------------------------------------------------------


@dataclass
class HPAStudyDesign:
    """Factorial design for the HPA constraint-transfer study."""

    cpu_targets: list[int] = field(default_factory=lambda: [50, 70, 90])
    max_replicas: list[int] = field(default_factory=lambda: [4, 6, 10])
    db_pools: list[int] = field(default_factory=lambda: [50, 100])
    workloads: list[str] = field(default_factory=lambda: ["ramp", "flash_sale", "sinusoidal"])
    repeats: int = 3
    heldout_fraction: float = 0.2
    duration: int = 40
    warmup_seconds: int = 5
    base_rps: int = 500
    min_replicas: int = 1
    consumers: int = 2
    cache_enabled: bool = True
    scale_down_stabilization_seconds: int = 15
    hpa_interval_seconds: int = 5
    settle_seconds: int = 3
    seed: int = 42

    def cells(self) -> list[dict[str, Any]]:
        """Returns every factorial cell (one unique HPA configuration)."""
        return [
            {
                "hpa_target_cpu": target,
                "hpa_max_replicas": max_rep,
                "db_pool_size": pool,
                "workload": workload,
            }
            for target, max_rep, pool, workload in itertools.product(
                self.cpu_targets, self.max_replicas, self.db_pools, self.workloads
            )
        ]

    def split_cells(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Partitions cells into (train, held-out) at cell level, so repeats stay together."""
        cells = self.cells()
        rng = np.random.default_rng(self.seed)
        order = np.arange(len(cells))
        rng.shuffle(order)
        n_heldout = max(1, int(round(len(cells) * self.heldout_fraction)))
        heldout_idx = set(order[:n_heldout].tolist())
        train = [cells[i] for i in range(len(cells)) if i not in heldout_idx]
        heldout = [cells[i] for i in sorted(heldout_idx)]
        return train, heldout


def tested_region(design: HPAStudyDesign) -> dict[str, Any]:
    """Documents exactly which region the HPA study covers."""
    return {
        "hpa_cpu_targets": design.cpu_targets,
        "hpa_max_replicas": design.max_replicas,
        "db_pool_sizes": design.db_pools,
        "workloads": design.workloads,
        "target_rps": design.base_rps,
        "checkout_replicas": f"dynamic {design.min_replicas}..max_replicas (set by HPA)",
        "cache_enabled": design.cache_enabled,
        "consumers": design.consumers,
        "duration_seconds": design.duration,
        "repeats": design.repeats,
    }


# ---------------------------------------------------------------------------
# Constraint loading
# ---------------------------------------------------------------------------


def _compile_evaluator(inequality: str):
    """Compiles a constraint inequality string into a vectorised pandas evaluator."""
    expr = inequality.replace("+ +", "+").replace("+ -", "- ")

    def _eval(df: pd.DataFrame) -> np.ndarray:
        try:
            return df.eval(expr, engine="python").to_numpy(dtype=bool)
        except Exception:  # pragma: no cover - defensive
            return np.zeros(len(df), dtype=bool)

    return _eval


def _constraint_from_dict(payload: dict[str, Any]) -> Constraint:
    return Constraint(
        variables=list(payload["variables"]),
        inequality=payload["inequality"],
        confidence=float(payload.get("confidence", 1.0)),
        support=float(payload.get("support", 0.0)),
        support_count=int(payload.get("support_count", 0)),
        source_experiments=list(payload.get("source_experiments", [])),
        z3_expr=payload.get("z3_expr", payload["inequality"]),
        method=payload.get("method", "decision_tree"),
        evaluator=_compile_evaluator(payload["inequality"]),
        heldout_precision=payload.get("heldout_precision"),
        heldout_recall=payload.get("heldout_recall"),
        heldout_f1=payload.get("heldout_f1"),
        heldout_support=payload.get("heldout_support"),
    )


def connection_invariant_constraint() -> Constraint:
    """Structural invariant: the deployment's connection demand must fit the pool."""
    expr = f"checkout_replicas * {CONNECTIONS_PER_REPLICA:.2f} - db_pool_size > 0"
    return Constraint(
        variables=["checkout_replicas", "db_pool_size"],
        inequality=expr,
        confidence=1.0,
        support=1.0,
        support_count=0,
        source_experiments=[],
        z3_expr=expr,
        method="structural",
        evaluator=_compile_evaluator(expr),
    )


def load_validated_constraints(
    path: str | Path = DEFAULT_CONSTRAINTS_PATH,
    include_connection_invariant: bool = True,
) -> list[tuple[str, str, Constraint]]:
    """Loads the validated constraint set without retraining it.

    Returns:
        List of ``(constraint_id, source, constraint)`` tuples. ``source`` is
        ``"learned"`` for Task 12 rules and ``"structural"`` for the connection
        invariant required by Task 14C.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Validated constraint set not found: {path}")

    payload = json.loads(path.read_text())
    records: list[tuple[str, str, Constraint]] = []
    for idx, item in enumerate(payload.get("top_constraints", []), start=1):
        records.append((f"C{idx:02d}", "learned", _constraint_from_dict(item)))

    if include_connection_invariant:
        records.append(("K01", "structural", connection_invariant_constraint()))
    return records


# ---------------------------------------------------------------------------
# HPA simulation with phase-labelled per-second telemetry
# ---------------------------------------------------------------------------


def _instantaneous_rps(workload: str, base_rps: int, second: int, duration: int) -> float:
    progress = second / max(1, duration)
    return max(1.0, base_rps * instantaneous_rps_fraction(workload, progress))


def _simulate_second(
    *,
    workload: str,
    rps: float,
    replicas: int,
    db_pool: int,
    cache: bool,
    consumers: int,
    seed: int,
) -> dict[str, float]:
    cfg = ExperimentConfig(
        workload=WorkloadSpec(rps=max(1, int(round(rps))), profile=workload, duration=1),
        checkout_replicas=max(1, replicas),
        db_pool_size=max(1, db_pool),
        cache_enabled=cache,
        consumers=consumers,
        seed=seed,
    )
    metrics = simulate_system_metrics(cfg, seed=seed)
    lats = metrics["client_side_latency_percentiles"]
    return {
        "p95_latency_ms": float(lats["p95"]),
        "p99_latency_ms": float(lats["p99"]),
        "error_rate_pct": float(metrics["error_rate_percent"]),
        "db_connections": float(metrics["db_connections"]),
        "cpu_utilization_pct": float(metrics["cpu_pct"]),
        "achieved_rps": float(metrics["achieved_rps"]),
    }


def simulate_hpa_run(
    cell: dict[str, Any],
    design: HPAStudyDesign,
    repeat: int = 0,
    seed: int | None = None,
) -> tuple[dict[str, Any], pd.DataFrame]:
    """Simulates one HPA-controlled run and returns (run summary, per-second rows).

    HPA mirrors the real controller: it reacts to CPU utilisation against the
    configured target, respects min/max replica bounds, doubles at most per
    interval, and waits ``scale_down_stabilization_seconds`` before shrinking.
    """
    if seed is not None:
        run_seed = seed
    else:
        key = (
            f"{cell['hpa_target_cpu']}|{cell['hpa_max_replicas']}|"
            f"{cell['db_pool_size']}|{cell['workload']}|{repeat}"
        )
        # Stable across processes (unlike the salted built-in hash()).
        digest = int(hashlib.sha256(key.encode()).hexdigest()[:8], 16)
        run_seed = design.seed + digest
    rng = np.random.default_rng(run_seed)

    target = int(cell["hpa_target_cpu"])
    max_rep = int(cell["hpa_max_replicas"])
    pool = int(cell["db_pool_size"])
    workload = str(cell["workload"])
    run_id = f"hpa_{workload[:4]}_{target}_{max_rep}_{pool}_r{repeat}"

    replicas = design.min_replicas
    last_change = -(10**9)
    scaling_events: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    transition_until = -1

    for second in range(design.duration):
        rps = _instantaneous_rps(workload, design.base_rps, second, design.duration)
        sample_seed = int(rng.integers(0, 2**31 - 1))
        metrics = _simulate_second(
            workload=workload,
            rps=rps,
            replicas=replicas,
            db_pool=pool,
            cache=design.cache_enabled,
            consumers=design.consumers,
            seed=sample_seed,
        )
        cpu = metrics["cpu_utilization_pct"]

        # HPA control loop
        replicas_before = replicas
        if second >= design.hpa_interval_seconds:
            if (
                cpu > target
                and replicas < max_rep
                and (second - last_change) >= design.hpa_interval_seconds
            ):
                desired = min(max_rep, max(replicas + 1, int(math.ceil(replicas * cpu / target))))
                # HPA "Percent 100 / period 15s" behaviour: at most double per action.
                desired = min(desired, replicas * 2, max_rep)
            elif (
                cpu < 0.8 * target
                and replicas > design.min_replicas
                and (second - last_change) >= design.scale_down_stabilization_seconds
                and second >= design.warmup_seconds
            ):
                desired = max(design.min_replicas, replicas - 1)
            else:
                desired = replicas

            if desired != replicas:
                scaling_events.append(
                    {
                        "timestamp_offset_seconds": second,
                        "event_type": "scale_up" if desired > replicas else "scale_down",
                        "from_replicas": replicas,
                        "to_replicas": desired,
                        "trigger_metric": "cpu",
                        "cpu_utilization_pct": round(cpu, 1),
                        "target_utilization_pct": target,
                        "reason": (
                            f"CPU {cpu:.1f}% {'>' if desired > replicas else '<'} target {target}%"
                        ),
                    }
                )
                replicas = desired
                last_change = second
                transition_until = max(transition_until, second + design.settle_seconds)

        demand = replicas * CONNECTIONS_PER_REPLICA
        in_transition = second < design.warmup_seconds or second <= transition_until
        rows.append(
            {
                "run_id": run_id,
                "second": second,
                "phase": "transition" if in_transition else "steady",
                "target_rps": round(rps, 1),
                "checkout_replicas": replicas,
                "db_pool_size": pool,
                "cache_enabled": design.cache_enabled,
                "consumers": design.consumers,
                "workload_profile": workload,
                "hpa_target_cpu": target,
                "hpa_max_replicas": max_rep,
                "replicas_before_second": replicas_before,
                "replica_changed": replicas != replicas_before,
                "cpu_utilization_pct": round(cpu, 1),
                "db_connection_demand": round(demand, 1),
                "db_connection_excess": round(max(0.0, demand - pool), 1),
                "connection_constraint_violated": demand > pool,
                "p95_latency_ms": metrics["p95_latency_ms"],
                "p99_latency_ms": metrics["p99_latency_ms"],
                "error_rate_pct": metrics["error_rate_pct"],
                "db_connections": metrics["db_connections"],
                "achieved_rps": metrics["achieved_rps"],
                "slo_violated": metrics["p95_latency_ms"] > VIOLATION_THRESHOLD_MS,
            }
        )

    frame = pd.DataFrame(rows)
    summary = {
        **cell,
        "run_id": run_id,
        "repeat": repeat,
        "seed": run_seed,
        "peak_replicas": int(frame["checkout_replicas"].max()),
        "mean_replicas": round(float(frame["checkout_replicas"].mean()), 2),
        "scaling_events": len(scaling_events),
        "scale_ups": sum(1 for e in scaling_events if e["event_type"] == "scale_up"),
        "scale_downs": sum(1 for e in scaling_events if e["event_type"] == "scale_down"),
        "steady_rows": int((frame["phase"] == "steady").sum()),
        "transition_rows": int((frame["phase"] == "transition").sum()),
        "p95_latency_ms": round(float(frame["p95_latency_ms"].max()), 1),
        "error_rate_pct": round(float(frame["error_rate_pct"].max()), 2),
        "connection_violation_rows": int(frame["connection_constraint_violated"].sum()),
        "connection_demand_peak": round(float(frame["db_connection_demand"].max()), 1),
    }
    return summary, frame


def build_hpa_dataset(
    design: HPAStudyDesign,
) -> tuple[pd.DataFrame, pd.DataFrame, list[dict[str, Any]]]:
    """Runs the full factorial study and returns (run summaries, rows, autoscaler events)."""
    train_cells, heldout_cells = design.split_cells()

    run_summaries: list[dict[str, Any]] = []
    frames: list[pd.DataFrame] = []
    all_events: list[dict[str, Any]] = []

    for cell, split in [
        *[(c, "train") for c in train_cells],
        *[(c, "held_out") for c in heldout_cells],
    ]:
        for repeat in range(design.repeats):
            summary, frame = simulate_hpa_run(cell, design, repeat=repeat)
            summary["split"] = split
            frame["split"] = split
            run_summaries.append(summary)
            frames.append(frame)

            for event in _events_for_run(frame, cell, design):
                event["split"] = split
                all_events.append(event)

    rows = pd.concat(frames, ignore_index=True)
    runs = pd.DataFrame(run_summaries)
    logger.info(
        "HPA study simulated: %d runs (%d cells x %d repeats, %d held-out cells)",
        len(runs),
        len(train_cells) + len(heldout_cells),
        design.repeats,
        len(heldout_cells),
    )
    return runs, rows, all_events


def _events_for_run(
    frame: pd.DataFrame, cell: dict[str, Any], design: HPAStudyDesign
) -> list[dict[str, Any]]:
    """Reconstructs scaling events from a run's replica transitions.

    The simulator records events inline; this helper re-derives them from the
    frame so they can be joined to rows by second without duplicating state.
    """
    events: list[dict[str, Any]] = []
    prev = int(frame.iloc[0]["checkout_replicas"])
    for _, row in frame.iterrows():
        cur = int(row["checkout_replicas"])
        if cur != prev:
            events.append(
                {
                    "run_id": row["run_id"],
                    "timestamp_offset_seconds": int(row["second"]),
                    "event_type": "scale_up" if cur > prev else "scale_down",
                    "from_replicas": prev,
                    "to_replicas": cur,
                    "hpa_target_cpu": cell["hpa_target_cpu"],
                    "hpa_max_replicas": cell["hpa_max_replicas"],
                    "db_pool_size": cell["db_pool_size"],
                    "workload": cell["workload"],
                    "cpu_utilization_pct": row["cpu_utilization_pct"],
                }
            )
        prev = cur
    return events


# ---------------------------------------------------------------------------
# Phase-wise constraint evaluation
# ---------------------------------------------------------------------------


@dataclass
class PhaseMetrics:
    """Precision/recall of one constraint on one phase of the study."""

    phase: str
    n_rows: int
    support: int
    tp: int
    fp: int
    fn: int
    tn: int
    precision: float
    recall: float
    f1: float
    accuracy: float
    mean_p95_when_matched: float
    mean_p95_when_unmatched: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "n_rows": self.n_rows,
            "support": self.support,
            "tp": self.tp,
            "fp": self.fp,
            "fn": self.fn,
            "tn": self.tn,
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1": round(self.f1, 4),
            "accuracy": round(self.accuracy, 4),
            "mean_p95_when_matched": round(self.mean_p95_when_matched, 2),
            "mean_p95_when_unmatched": round(self.mean_p95_when_unmatched, 2),
        }


def evaluate_constraint(
    constraint: Constraint,
    df: pd.DataFrame,
    phase: str,
    target_col: str = "p95_latency_ms",
    threshold: float = VIOLATION_THRESHOLD_MS,
) -> PhaseMetrics:
    """Directly checks a rule's prediction against observed SLO violations."""
    subset = df if phase == "all" else df[df["phase"] == phase]
    n = len(subset)
    if n == 0:
        return PhaseMetrics(phase, 0, 0, 0, 0, 0, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

    mask = np.asarray(constraint.matches(subset), dtype=bool)
    actual = subset[target_col].to_numpy(dtype=float) > threshold

    tp = int(np.sum(mask & actual))
    fp = int(np.sum(mask & ~actual))
    fn = int(np.sum(~mask & actual))
    tn = int(np.sum(~mask & ~actual))

    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    accuracy = (tp + tn) / n if n else 0.0

    matched_p95 = float(subset.loc[mask, target_col].mean()) if mask.any() else 0.0
    unmatched_p95 = float(subset.loc[~mask, target_col].mean()) if (~mask).any() else 0.0

    return PhaseMetrics(
        phase=phase,
        n_rows=n,
        support=int(mask.sum()),
        tp=tp,
        fp=fp,
        fn=fn,
        tn=tn,
        precision=precision,
        recall=recall,
        f1=f1,
        accuracy=accuracy,
        mean_p95_when_matched=matched_p95,
        mean_p95_when_unmatched=unmatched_p95,
    )


# ---------------------------------------------------------------------------
# Boundary estimation (new bound + bootstrap CI)
# ---------------------------------------------------------------------------


@dataclass
class BoundEstimate:
    """Estimated violation boundary for a constraint, with a bootstrap CI."""

    statistic: str
    comparator: str
    old_bound: float
    new_bound: float | None
    ci_low: float | None
    ci_high: float | None
    f1: float
    precision: float
    recall: float
    rows_used: int
    estimable: bool
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "statistic": self.statistic,
            "comparator": self.comparator,
            "old_bound": round(self.old_bound, 4),
            "new_bound": round(self.new_bound, 4) if self.new_bound is not None else None,
            "ci_low": round(self.ci_low, 4) if self.ci_low is not None else None,
            "ci_high": round(self.ci_high, 4) if self.ci_high is not None else None,
            "f1": round(self.f1, 4),
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "rows_used": self.rows_used,
            "estimable": self.estimable,
            "note": self.note,
        }


_LINEAR_TERM = re.compile(r"([+-]?\s*[\d.]+)\s*\*\s*(\w+)")
_SIMPLE_CLAUSE = re.compile(r"(\w+)\s*(<=|>=|<|>)\s*(-?[\d.]+)")


def _linear_spec(inequality: str) -> tuple[dict[str, float], float] | None:
    """Parses ``a*x + b*y >= c`` into ({x: a, y: b}, c)."""
    if "*" not in inequality:
        return None
    if ">=" not in inequality:
        return None
    lhs, rhs = inequality.rsplit(">=", 1)
    coefs: dict[str, float] = {}
    for amount, variable in _LINEAR_TERM.findall(lhs):
        coefs[variable] = coefs.get(variable, 0.0) + float(amount.replace(" ", ""))
    try:
        bound = float(rhs.strip())
    except ValueError:
        return None
    if not coefs:
        return None
    return coefs, bound


def _best_threshold(
    values: np.ndarray, labels: np.ndarray, direction: str
) -> tuple[float | None, float, float, float, float | None, float | None]:
    """Finds the F1-optimal one-sided threshold and its identifiability interval.

    A one-sided threshold is only identified up to a plateau: every cut inside a
    gap between adjacent observed values predicts exactly the same rows and
    therefore has exactly the same F1. The returned ``bound`` is the midpoint of
    that plateau and ``(low, high)`` is its extent, which is a more honest
    uncertainty statement than a single point.

    Returns:
        ``(bound, f1, precision, recall, plateau_low, plateau_high)``; all
        ``None``/``0`` when the rows are single-class.
    """
    values = np.asarray(values, dtype=float)
    labels = np.asarray(labels, dtype=int)
    if len(values) == 0 or labels.sum() == 0 or labels.sum() == len(labels):
        return None, 0.0, 0.0, 0.0, None, None

    order = np.argsort(values, kind="mergesort")
    sorted_values = values[order]
    sorted_labels = labels[order]
    total_pos = int(sorted_labels.sum())

    candidates = np.unique(sorted_values)
    scores: list[tuple[float, float, float, float]] = []
    for candidate in candidates:
        if direction == ">":
            # Predict violation when value > candidate.
            pos = int(np.searchsorted(sorted_values, candidate, side="right"))
            tp = int(sorted_labels[pos:].sum())
            fp = int((len(sorted_labels) - pos) - tp)
        else:
            # Predict violation when value < candidate.
            pos = int(np.searchsorted(sorted_values, candidate, side="left"))
            tp = int(sorted_labels[:pos].sum())
            fp = int(pos - tp)

        fn = total_pos - tp  # noqa: F841 - kept for symmetry with the confusion matrix
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / total_pos if total_pos else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
        scores.append((float(candidate), f1, precision, recall))

    best_f1 = max(score[1] for score in scores)
    if best_f1 <= 0.0:
        return None, 0.0, 0.0, 0.0, None, None

    # Every threshold achieving the maximum F1 is equally supported by the data.
    plateau = [score for score in scores if score[1] >= best_f1 - 1e-9]
    low = plateau[0][0]
    high = plateau[-1][0]
    bound = 0.5 * (low + high)
    # Report the precision/recall of the midpoint threshold (all plateau members
    # predict the rows that give the maximum F1, so these are identical).
    mid_index = min(range(len(scores)), key=lambda i: abs(scores[i][0] - bound))
    _, f1, precision, recall = scores[mid_index]
    return bound, f1, precision, recall, low, high


def estimate_bound(
    constraint: Constraint,
    df: pd.DataFrame,
    target_col: str = "p95_latency_ms",
    threshold: float = VIOLATION_THRESHOLD_MS,
    n_boot: int = 200,
    seed: int = 42,
) -> BoundEstimate:
    """Estimates where the violation boundary now sits for *constraint*.

    Sparse-regression rules are reduced to their linear score; decision-tree
    rules are reduced to the one-sided numeric clause that drives the violation
    (other clauses become a context filter). The new cut point maximises F1 on
    the supplied rows and a bootstrap gives a 95% percentile interval.
    """
    if df.empty:
        return BoundEstimate("", "", 0.0, None, None, None, 0.0, 0.0, 0.0, 0, False, "no rows")

    work = df
    linear = _linear_spec(constraint.inequality)

    if linear is not None:
        coefs, old_bound = linear
        statistic_name = "score"
        direction = ">"
        missing = [v for v in coefs if v not in work.columns]
        if missing:
            return BoundEstimate(
                statistic_name,
                direction,
                old_bound,
                None,
                None,
                None,
                0.0,
                0.0,
                0.0,
                len(work),
                False,
                f"missing columns {missing}",
            )
        values = np.zeros(len(work), dtype=float)
        for variable, coef in coefs.items():
            values += coef * work[variable].to_numpy(dtype=float)
        labels = (work[target_col].to_numpy(dtype=float) > threshold).astype(int)
        note = "bound is on the rule's linear score, not a single variable"
    else:
        clauses = _SIMPLE_CLAUSE.findall(constraint.inequality)
        if not clauses:
            return BoundEstimate(
                "", "", 0.0, None, None, None, 0.0, 0.0, 0.0, len(work), False, "unparseable rule"
            )

        increasing = [c for c in clauses if c[1] in (">", ">=") and c[0] != "cache_enabled"]
        critical = increasing[0] if increasing else clauses[0]
        statistic_name, comparator, raw_bound = critical
        old_bound = float(raw_bound)
        direction = ">" if comparator in (">", ">=") else "<"

        context = [c for c in clauses if c is not critical]
        if context:
            context_expr = " and ".join(f"{v} {op} {val}" for v, op, val in context)
            try:
                mask = work.eval(context_expr, engine="python").to_numpy(dtype=bool)
            except Exception:  # pragma: no cover - defensive
                mask = None
            if mask is not None:
                if int(mask.sum()) < 10:
                    return BoundEstimate(
                        statistic_name,
                        direction,
                        old_bound,
                        None,
                        None,
                        None,
                        0.0,
                        0.0,
                        0.0,
                        int(mask.sum()),
                        False,
                        "context clauses not satisfied in the tested HPA region",
                    )
                work = work[mask]
        if statistic_name not in work.columns:
            return BoundEstimate(
                statistic_name,
                direction,
                old_bound,
                None,
                None,
                None,
                0.0,
                0.0,
                0.0,
                len(work),
                False,
                "missing column",
            )
        values = work[statistic_name].to_numpy(dtype=float)
        labels = (work[target_col].to_numpy(dtype=float) > threshold).astype(int)
        note = "" if len(context) == 0 or len(work) == len(df) else "context clauses used as filter"

    new_bound, f1, precision, recall, plateau_low, plateau_high = _best_threshold(
        values, labels, direction
    )
    if new_bound is None:
        class_note = (
            "no observed SLO violations in these rows"
            if labels.sum() == 0
            else "all rows are SLO violations (boundary is below the tested region)"
        )
        return BoundEstimate(
            statistic_name,
            direction,
            old_bound,
            None,
            None,
            None,
            0.0,
            0.0,
            0.0,
            len(values),
            False,
            class_note,
        )

    # Bootstrap the identifiability interval, not just the point estimate: a
    # resample can shift where the F1-optimal plateau sits, and that movement is
    # the right source of uncertainty for a boundary claim.
    rng = np.random.default_rng(seed)
    boot_low: list[float] = []
    boot_high: list[float] = []
    for _ in range(n_boot):
        idx = rng.integers(0, len(values), len(values))
        _, _, _, _, b_low, b_high = _best_threshold(values[idx], labels[idx], direction)
        if b_low is not None and b_high is not None:
            boot_low.append(b_low)
            boot_high.append(b_high)
    if boot_low:
        ci_low = float(
            min(np.percentile(boot_low, 2.5), plateau_low if plateau_low is not None else new_bound)
        )
        ci_high = float(
            max(
                np.percentile(boot_high, 97.5),
                plateau_high if plateau_high is not None else new_bound,
            )
        )
    else:
        ci_low = plateau_low if plateau_low is not None else new_bound
        ci_high = plateau_high if plateau_high is not None else new_bound

    return BoundEstimate(
        statistic=statistic_name,
        comparator=direction,
        old_bound=old_bound,
        new_bound=new_bound,
        ci_low=ci_low,
        ci_high=ci_high,
        f1=f1,
        precision=precision,
        recall=recall,
        rows_used=len(values),
        estimable=True,
        note=note,
    )


def ci_excludes(bound: BoundEstimate, value: float) -> bool:
    """True when *value* lies outside the estimated boundary's 95% CI."""
    if not bound.estimable or bound.ci_low is None or bound.ci_high is None:
        return False
    return value < bound.ci_low or value > bound.ci_high


# ---------------------------------------------------------------------------
# Connection-invariant violation tracing
# ---------------------------------------------------------------------------


def trace_connection_violations(
    rows: pd.DataFrame,
    events: list[dict[str, Any]],
    threshold: float = VIOLATION_THRESHOLD_MS,
) -> list[dict[str, Any]]:
    """Records each connection-invariant violation and the scaling action behind it.

    A violation is ``replicas * connections_per_replica > db_pool_size``. The
    causally responsible action is the most recent scale-up in the same run at or
    before the violating second.
    """
    events_by_run: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        if event["event_type"] == "scale_up":
            events_by_run.setdefault(event["run_id"], []).append(event)

    violations: list[dict[str, Any]] = []
    violating = rows[rows["connection_constraint_violated"]]
    for _, row in violating.iterrows():
        candidates = [
            e
            for e in events_by_run.get(row["run_id"], [])
            if e["timestamp_offset_seconds"] <= row["second"]
        ]
        cause = max(candidates, key=lambda e: e["timestamp_offset_seconds"]) if candidates else None
        violations.append(
            {
                "run_id": row["run_id"],
                "split": row.get("split", ""),
                "workload": row["workload_profile"],
                "hpa_target_cpu": int(row["hpa_target_cpu"]),
                "hpa_max_replicas": int(row["hpa_max_replicas"]),
                "db_pool_size": int(row["db_pool_size"]),
                "second": int(row["second"]),
                "phase": row["phase"],
                "replicas": int(row["checkout_replicas"]),
                "connection_demand": float(row["db_connection_demand"]),
                "connection_excess": float(row["db_connection_excess"]),
                "p95_latency_ms": float(row["p95_latency_ms"]),
                "slo_violated": bool(row["p95_latency_ms"] > threshold),
                "caused_by": (
                    {
                        "event_type": cause["event_type"],
                        "second": cause["timestamp_offset_seconds"],
                        "from_replicas": cause["from_replicas"],
                        "to_replicas": cause["to_replicas"],
                        "cpu_utilization_pct": cause["cpu_utilization_pct"],
                        "hpa_target_cpu": cause["hpa_target_cpu"],
                        "hpa_max_replicas": cause["hpa_max_replicas"],
                    }
                    if cause
                    else None
                ),
            }
        )
    return violations


def summarise_connection_violations(violations: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregates connection violations by configuration and by causal action."""
    if not violations:
        return {
            "total_violation_rows": 0,
            "affected_runs": 0,
            "by_configuration": [],
            "causal_actions": [],
            "note": (
                "No connection-invariant violation occurred: HPA never scaled replicas "
                f"beyond floor(db_pool / {CONNECTIONS_PER_REPLICA:g})."
            ),
        }

    frame = pd.DataFrame(violations)
    grouped = (
        frame.groupby(["workload", "hpa_target_cpu", "hpa_max_replicas", "db_pool_size"])
        .agg(
            violation_rows=("second", "count"),
            first_violating_second=("second", "min"),
            peak_replicas=("replicas", "max"),
            peak_demand=("connection_demand", "max"),
            slo_violations=("slo_violated", "sum"),
        )
        .reset_index()
    )
    causes: dict[str, int] = {}
    for violation in violations:
        cause = violation.get("caused_by")
        if not cause:
            continue
        key = (
            f"scale_up {cause['from_replicas']}->{cause['to_replicas']} "
            f"at t={cause['second']}s (CPU {cause['cpu_utilization_pct']}% vs "
            f"target {cause['hpa_target_cpu']}%, max {cause['hpa_max_replicas']})"
        )
        causes[key] = causes.get(key, 0) + 1
    return {
        "total_violation_rows": len(violations),
        "affected_runs": int(frame["run_id"].nunique()),
        "slo_violating_rows": int(frame["slo_violated"].sum()),
        "by_configuration": grouped.to_dict(orient="records"),
        "causal_actions": [
            {"action": action, "violating_rows": count}
            for action, count in sorted(causes.items(), key=lambda kv: -kv[1])
        ],
        "examples": violations[:10],
        "note": (
            "HPA scaled replicas past the pool's connection budget. The queuing "
            "simulator clamps active connections at the pool size, so a structural "
            "connection violation is a configuration-level failure and does not "
            "automatically appear as a p95 latency violation."
        ),
    }


# ---------------------------------------------------------------------------
# Constraint classification
# ---------------------------------------------------------------------------


@dataclass
class ConstraintTransfer:
    """Transfer result for a single validated constraint under HPA."""

    constraint_id: str
    source: str
    rule: str
    variables: list[str]
    classification: str
    steady: PhaseMetrics
    transition: PhaseMetrics
    baseline_precision: float | None
    baseline_recall: float | None
    bound: BoundEstimate | None
    region: dict[str, Any]
    evidence: str
    notes: str = ""
    refit_experiments: int = 0
    refit_ci_after: list[float] | None = None
    connection_violations: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "constraint_id": self.constraint_id,
            "source": self.source,
            "rule": self.rule,
            "variables": self.variables,
            "classification": self.classification,
            "baseline_static_precision": self.baseline_precision,
            "baseline_static_recall": self.baseline_recall,
            "steady": self.steady.to_dict(),
            "transition": self.transition.to_dict(),
            "bound": self.bound.to_dict() if self.bound else None,
            "refit_experiments": self.refit_experiments,
            "refit_ci_after": self.refit_ci_after,
            "connection_violations": self.connection_violations,
            "tested_region": self.region,
            "evidence": self.evidence,
            "notes": self.notes,
        }


def _classify(
    *,
    source: str,
    steady: PhaseMetrics,
    transition: PhaseMetrics,
    baseline_precision: float | None,
    bound: BoundEstimate | None,
    connection_violations: int,
) -> tuple[str, str]:
    """Deterministic classification with a human-readable evidence string."""
    if source == "structural" and connection_violations > 0:
        return (
            CLASS_VIOLATED,
            f"{connection_violations} violating rows; HPA scale-up exceeded pool/{CONNECTIONS_PER_REPLICA:g}",
        )

    if (
        bound is not None
        and bound.estimable
        and bound.new_bound is not None
        and bound.f1 >= 0.5
        and ci_excludes(bound, bound.old_bound)
        and _relative_bound_shift(bound) >= MIN_RELATIVE_BOUND_SHIFT
    ):
        support_note = (
            " Previous condition has zero support in the HPA region, so only the "
            "re-fitted bound is usable there."
            if steady.support == 0
            else ""
        )
        return (
            CLASS_SHIFTED,
            f"new bound {bound.new_bound:.3g} with 95% CI "
            f"[{bound.ci_low:.3g}, {bound.ci_high:.3g}] excludes old "
            f"{bound.old_bound:.3g} ({_relative_bound_shift(bound) * 100:.0f}% shift)."
            f"{support_note}",
        )

    if steady.support == 0 and transition.support == 0:
        return CLASS_DISAPPEARED, "rule has zero support in the tested HPA region"

    baseline = baseline_precision if baseline_precision is not None else 0.0
    if steady.support == 0:
        return CLASS_DISAPPEARED, "rule only fires in transition rows (support=0 in steady state)"
    if steady.precision < 0.5 and baseline >= 0.75:
        return (
            CLASS_DISAPPEARED,
            f"precision collapsed {baseline:.2f} -> {steady.precision:.2f} on steady rows",
        )

    if steady.precision >= 0.5:
        return (
            CLASS_UNCHANGED,
            f"precision {steady.precision:.2f} (static {baseline:.2f}), recall {steady.recall:.2f}, "
            f"support {steady.support}",
        )
    return (
        CLASS_DISAPPEARED,
        f"rule does not predict observed violations (precision {steady.precision:.2f}, "
        f"static {baseline:.2f})",
    )


# ---------------------------------------------------------------------------
# Transient-rule discovery
# ---------------------------------------------------------------------------


def _mask_overlap(a: np.ndarray, b: np.ndarray) -> float:
    union = np.sum(a | b)
    return float(np.sum(a & b) / union) if union else 0.0


def discover_transient_constraints(
    df_train: pd.DataFrame,
    df_heldout: pd.DataFrame,
    validated: list[tuple[str, str, Constraint]],
    target_col: str = "p95_latency_ms",
    threshold: float = VIOLATION_THRESHOLD_MS,
    min_transition_precision: float = 0.7,
    min_transition_support: int = 15,
) -> list[Constraint]:
    """Learns rules that exist only in transition rows.

    A rule qualifies as *new (transient)* when it has real support and precision
    during transitions, is not a duplicate of a validated rule, and loses most of
    its support or precision once the replicas settle.
    """
    transition_train = df_train[df_train["phase"] == "transition"]
    transition_heldout = df_heldout[df_heldout["phase"] == "transition"]
    if len(transition_train) < 30 or len(transition_heldout) < 10:
        return []

    candidates = learn_decision_tree_constraints(
        df_train=transition_train,
        target_col=target_col,
        threshold=threshold,
        max_depths=[2, 3],
        min_samples_leaf=10,
        min_confidence=0.8,
    )

    all_rows = pd.concat([df_train, df_heldout], ignore_index=True)
    validated_masks = [np.asarray(c.matches(all_rows), dtype=bool) for _, _, c in validated]

    transient: list[Constraint] = []
    for candidate in candidates:
        candidate_mask = np.asarray(candidate.matches(all_rows), dtype=bool)
        if any(_mask_overlap(candidate_mask, mask) >= 0.9 for mask in validated_masks):
            continue
        metrics = evaluate_constraint(
            candidate, transition_heldout, "transition", target_col, threshold
        )
        if metrics.support < min_transition_support or metrics.precision < min_transition_precision:
            continue
        steady_metrics = evaluate_constraint(candidate, df_heldout, "steady", target_col, threshold)
        transient_enough = (
            steady_metrics.support < max(5, int(0.3 * metrics.support))
            or (metrics.precision - steady_metrics.precision) >= 0.15
        )
        if transient_enough:
            candidate.heldout_precision = metrics.precision
            candidate.heldout_recall = metrics.recall
            candidate.heldout_f1 = metrics.f1
            candidate.heldout_support = metrics.support
            transient.append(candidate)

    return transient


# ---------------------------------------------------------------------------
# GP-guided re-fit of shifted constraints
# ---------------------------------------------------------------------------


def _candidate_pool(
    design: HPAStudyDesign,
    bound: BoundEstimate,
    n_rps: int = 5,
) -> pd.DataFrame:
    """Builds an unrun candidate pool concentrated around the shifted boundary."""
    rows: list[dict[str, Any]] = []
    if bound.estimable and bound.new_bound is not None and bound.statistic == "target_rps":
        center = bound.new_bound
        span = max(25.0, 0.15 * center)
        rps_grid = np.linspace(max(25.0, center - span), center + span, n_rps)
    else:
        rps_grid = np.linspace(0.3 * design.base_rps, design.base_rps, n_rps)

    for rps in rps_grid:
        for target in design.cpu_targets:
            for max_rep in sorted({*design.max_replicas, 8}):
                for pool in design.db_pools:
                    for workload in design.workloads:
                        rows.append(
                            {
                                "target_rps": float(rps),
                                "checkout_replicas": min(
                                    max_rep, max(design.min_replicas, int(math.ceil(rps / 70.0)))
                                ),
                                "db_pool_size": pool,
                                "cache_enabled": design.cache_enabled,
                                "consumers": design.consumers,
                                "workload_profile": workload,
                                "hpa_target_cpu": target,
                                "hpa_max_replicas": max_rep,
                            }
                        )
    return pd.DataFrame(rows)


def _gp_acquisition(mean: np.ndarray, std: np.ndarray, threshold: float) -> np.ndarray:
    """Weighted-uncertainty acquisition (same form as SLOBoundaryAcquisition)."""
    variance = np.clip(std**2, 1e-9, None)
    return std * np.exp(-0.5 * ((mean - threshold) ** 2) / variance)


def refit_shifted_bound(
    record: ConstraintTransfer,
    design: HPAStudyDesign,
    df_train: pd.DataFrame,
    budget: int = 5,
    gp_iter: int = 40,
    ci_tolerance: float = 0.02,
    seed: int = 42,
) -> dict[str, Any]:
    """Adds the minimum number of GP-selected experiments needed to pin a new bound."""
    if record.bound is None or not record.bound.estimable:
        return {
            "constraint_id": record.constraint_id,
            "experiments_used": 0,
            "reason": "bound not estimable",
        }

    observed = df_train.copy()
    pool = _candidate_pool(design, record.bound)
    if pool.empty:
        return {
            "constraint_id": record.constraint_id,
            "experiments_used": 0,
            "reason": "empty candidate pool",
        }

    rng = np.random.default_rng(seed)
    used = 0
    history: list[dict[str, Any]] = []

    def _ci_width(bound: BoundEstimate) -> float:
        if bound.ci_low is None or bound.ci_high is None:
            return math.inf
        return abs(bound.ci_high - bound.ci_low)

    current = record.bound
    # Stop as soon as the boundary's 95% interval is within +/-1% of the new
    # bound (ci_tolerance is the full relative width). When the interval is
    # already that tight, zero extra experiments are used.
    target_width = ci_tolerance * max(1e-6, abs(current.new_bound or 0.0))
    while used < budget and _ci_width(current) > target_width:
        train_sample = observed.sample(
            min(len(observed), 300), random_state=int(rng.integers(0, 2**31 - 1))
        )
        gp = GPSurrogate(target="p95_latency_ms", n_iter=gp_iter)
        try:
            gp.fit(train_sample)
            mean, std = gp.predict(pool)
        except Exception as exc:  # pragma: no cover - numerical guard
            logger.warning("GP re-fit unavailable for %s: %s", record.constraint_id, exc)
            return {
                "constraint_id": record.constraint_id,
                "experiments_used": used,
                "reason": f"GP failed: {exc}",
                "history": history,
            }

        scores = _gp_acquisition(mean, std, VIOLATION_THRESHOLD_MS)
        chosen_idx = int(np.argmax(scores))
        candidate = pool.iloc[chosen_idx].to_dict()

        cell = {
            "hpa_target_cpu": int(candidate["hpa_target_cpu"]),
            "hpa_max_replicas": int(candidate["hpa_max_replicas"]),
            "db_pool_size": int(candidate["db_pool_size"]),
            "workload": str(candidate["workload_profile"]),
        }
        # One extra repeat only: minimum extra experimentation.
        _, frame = simulate_hpa_run(cell, design, repeat=99 + used, seed=seed + used)
        extra = frame[frame["phase"] == "steady"].copy()
        if not extra.empty:
            observed = pd.concat([observed, extra], ignore_index=True)

        used += 1
        current = estimate_bound(
            _constraint_from_record(record),
            observed[observed["phase"] == "steady"],
            seed=seed + used,
        )
        history.append(
            {
                "experiment": used,
                "selected_config": cell,
                "gp_uncertainty": round(float(std[chosen_idx]), 2),
                "gp_predicted_p95": round(float(mean[chosen_idx]), 1),
                "bound_after": current.to_dict(),
            }
        )
        logger.info(
            "  [%s] extra experiment %d/.. at %s -> bound %.2f CI [%s, %s]",
            record.constraint_id,
            used,
            cell,
            current.new_bound if current.new_bound is not None else float("nan"),
            f"{current.ci_low:.1f}" if current.ci_low is not None else "n/a",
            f"{current.ci_high:.1f}" if current.ci_high is not None else "n/a",
        )

    return {
        "constraint_id": record.constraint_id,
        "experiments_used": used,
        "ci_width_before": round(_ci_width(record.bound), 2) if record.bound.estimable else None,
        "ci_width_after": round(_ci_width(current), 2),
        "target_ci_width": round(target_width, 4),
        "budget_exhausted": bool(used >= budget and _ci_width(current) > target_width),
        "bound_after": current.to_dict(),
        "history": history,
    }


def _constraint_from_record(record: ConstraintTransfer) -> Constraint:
    """Rebuilds a Constraint object from a transfer record."""
    return Constraint(
        variables=record.variables,
        inequality=record.rule,
        confidence=1.0,
        support=0.0,
        support_count=0,
        source_experiments=[],
        z3_expr=record.rule,
        method="sparse_regression"
        if "*" in record.rule and ">=" in record.rule
        else "decision_tree",
        evaluator=_compile_evaluator(record.rule),
    )


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


@dataclass
class HPAStudyResult:
    """Complete result of the HPA constraint-transfer study."""

    design: HPAStudyDesign
    runs: pd.DataFrame
    rows: pd.DataFrame
    events: list[dict[str, Any]]
    transfers: list[ConstraintTransfer]
    new_transient: list[Constraint]
    connection_summary: dict[str, Any]
    refits: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": "14C",
            "model_note": (
                "All runs are simulated with scopex.storage.dataset.simulate_system_metrics "
                "(queuing/capacity model). This is a model-level transfer study, not a "
                "hardware measurement."
            ),
            "design": {
                "cpu_targets": self.design.cpu_targets,
                "max_replicas": self.design.max_replicas,
                "db_pools": self.design.db_pools,
                "workloads": self.design.workloads,
                "repeats": self.design.repeats,
                "heldout_fraction": self.design.heldout_fraction,
                "cells": len(self.design.cells()),
                "runs": len(self.runs),
                "total_seconds_rows": len(self.rows),
                "heldout_cells": len(self.design.split_cells()[1]),
            },
            "tested_region": tested_region(self.design),
            "constraints": [t.to_dict() for t in self.transfers],
            "new_transient_constraints": [c.to_dict() for c in self.new_transient],
            "connection_invariant": self.connection_summary,
            "refits": self.refits,
        }


def run_hpa_transfer_study(
    design: HPAStudyDesign | None = None,
    constraints_path: str | Path = DEFAULT_CONSTRAINTS_PATH,
    refit_budget: int = 5,
    refit: bool = True,
) -> HPAStudyResult:
    """Executes the factorial HPA study, classifies every constraint, and re-fits shifted ones."""
    design = design or HPAStudyDesign()
    validated = load_validated_constraints(constraints_path)
    logger.info("Loaded %d validated constraints (no retraining)", len(validated))

    runs, rows, events = build_hpa_dataset(design)
    rows_train = rows[rows["split"] == "train"]
    rows_heldout = rows[rows["split"] == "held_out"]
    steady_train = rows_train[rows_train["phase"] == "steady"]

    violations = trace_connection_violations(rows, events)
    connection_summary = summarise_connection_violations(violations)

    region = tested_region(design)
    transfers: list[ConstraintTransfer] = []

    for constraint_id, source, constraint in validated:
        steady = evaluate_constraint(constraint, rows_heldout, "steady")
        transition = evaluate_constraint(constraint, rows_heldout, "transition")
        # The structural invariant's "bound" is regime-dependent (pool / conns per
        # replica), so it is evidenced by violation counts rather than a cut point.
        bound = None if source == "structural" else estimate_bound(constraint, steady_train)

        conn_viol = len(violations) if source == "structural" else 0

        classification, evidence = _classify(
            source=source,
            steady=steady,
            transition=transition,
            baseline_precision=constraint.heldout_precision,
            bound=bound,
            connection_violations=conn_viol,
        )

        notes = []
        if source == "structural":
            notes.append(
                "Invariant derived from the simulator's calibrated connection demand "
                f"({CONNECTIONS_PER_REPLICA:g} per replica); not a learned statistical rule."
            )
        if constraint.heldout_precision is None:
            notes.append("No static held-out precision recorded in the validated set.")
        if "cache_enabled" in constraint.variables:
            notes.append(
                "cache_enabled is fixed True in the HPA region, so cache-off branches cannot be "
                "exercised; disappearance here is a region limitation, not an HPA effect."
            )
        if classification == CLASS_DISAPPEARED:
            notes.append(
                f"Tested region: RPS 0.3-1.0 x {design.base_rps}, replicas dynamic "
                f"{design.min_replicas}-{max(design.max_replicas)}, pool {design.db_pools}, "
                f"workloads {design.workloads}, cache on."
            )

        transfers.append(
            ConstraintTransfer(
                constraint_id=constraint_id,
                source=source,
                rule=constraint.inequality,
                variables=list(constraint.variables),
                classification=classification,
                steady=steady,
                transition=transition,
                baseline_precision=constraint.heldout_precision,
                baseline_recall=constraint.heldout_recall,
                bound=bound,
                region=region,
                evidence=evidence,
                notes=" ".join(notes),
                connection_violations=conn_viol,
            )
        )

    new_transient = discover_transient_constraints(rows_train, rows_heldout, validated)
    logger.info("Discovered %d new transient constraints", len(new_transient))

    refits: list[dict[str, Any]] = []
    if refit:
        for record in transfers:
            if record.classification != CLASS_SHIFTED or record.source != "learned":
                continue
            refit_result = refit_shifted_bound(record, design, steady_train, budget=refit_budget)
            record.refit_experiments = int(refit_result.get("experiments_used", 0))
            after = refit_result.get("bound_after") or {}
            if after.get("ci_low") is not None:
                record.refit_ci_after = [after["ci_low"], after["ci_high"]]
            if record.refit_experiments:
                ci_after = (
                    f"[{after['ci_low']:.3g}, {after['ci_high']:.3g}]"
                    if after.get("ci_low") is not None
                    else "n/a"
                )
                record.notes = (
                    f"{record.notes} Re-fit used {record.refit_experiments} GP-selected "
                    f"extra run(s); CI after re-fit {ci_after}"
                    + (
                        " (budget exhausted before reaching target CI width)."
                        if refit_result.get("budget_exhausted")
                        else "."
                    )
                ).strip()
            refits.append(refit_result)

    return HPAStudyResult(
        design=design,
        runs=runs,
        rows=rows,
        events=events,
        transfers=transfers,
        new_transient=new_transient,
        connection_summary=connection_summary,
        refits=refits,
    )


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _fmt_phase(metrics: PhaseMetrics) -> str:
    return f"P {metrics.precision:.2f} / R {metrics.recall:.2f} (n={metrics.support})"


def format_transfer_table(result: HPAStudyResult) -> str:
    """Renders the acceptance table: one row per constraint."""
    lines: list[str] = []
    lines.append(
        "| ID | Constraint (condition => SLO violation) | Class | Steady P/R (n) | Transition P/R (n) | Old -> New bound [95% CI] | Refit exps | Evidence / notes |"
    )
    lines.append("|---|---|---|---|---|---|---|---|")

    for record in result.transfers:
        if record.bound and record.bound.estimable:
            bound_str = (
                f"{record.bound.old_bound:.2f} -> {record.bound.new_bound:.2f} "
                f"[{record.bound.ci_low:.2f}, {record.bound.ci_high:.2f}]"
            )
        elif record.bound:
            bound_str = f"{record.bound.old_bound:.2f} -> n/a ({record.bound.note})"
        else:
            bound_str = "n/a"

        exps = record.refit_experiments if record.classification == CLASS_SHIFTED else 0
        evidence = record.evidence
        if record.notes:
            separator = " " if evidence.endswith(".") else ". "
            evidence = f"{evidence}{separator}{record.notes}"

        lines.append(
            f"| {record.constraint_id} | `{record.rule}` | **{record.classification}** | "
            f"{_fmt_phase(record.steady)} | {_fmt_phase(record.transition)} | {bound_str} | "
            f"{exps} | {evidence} |"
        )

    for idx, constraint in enumerate(result.new_transient, start=1):
        lines.append(
            f"| N{idx:02d} | `{constraint.inequality}` | **{CLASS_NEW_TRANSIENT}** | "
            f"n/a | P {constraint.heldout_precision:.2f} / R {constraint.heldout_recall:.2f} "
            f"(n={constraint.heldout_support}) | n/a | 0 | "
            f"Learned on transition rows only; absent/weaker in steady state |"
        )

    return "\n".join(lines)


def format_connection_report(result: HPAStudyResult) -> str:
    """Renders the connection-invariant section of the report."""
    summary = result.connection_summary
    lines = ["## Connection invariant: replicas x connections-per-replica vs pool", ""]
    lines.append(f"- Invariant: `checkout_replicas * {CONNECTIONS_PER_REPLICA:g} <= db_pool_size`")
    lines.append(f"- Total violating rows: **{summary['total_violation_rows']}**")
    lines.append(f"- Affected runs: **{summary['affected_runs']}**")
    if "slo_violating_rows" in summary:
        lines.append(
            f"- Violating rows that also breached the latency SLO: "
            f"**{summary['slo_violating_rows']}**"
        )
    if summary["total_violation_rows"] == 0:
        lines.append(f"- {summary.get('note', 'No violations.')}")
        return "\n".join(lines)

    lines.append("")
    lines.append(
        "| Workload | CPU target | Max replicas | Pool | Violating rows | First t (s) | Peak replicas | Peak demand | SLO violated rows |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for row in summary["by_configuration"]:
        lines.append(
            f"| {row['workload']} | {row['hpa_target_cpu']} | {row['hpa_max_replicas']} | "
            f"{row['db_pool_size']} | {row['violation_rows']} | {row['first_violating_second']} | "
            f"{row['peak_replicas']} | {row['peak_demand']} | {row['slo_violations']} |"
        )

    if summary["causal_actions"]:
        lines.append("")
        lines.append("Causal autoscaler actions (scale-ups immediately preceding violations):")
        for action in summary["causal_actions"][:8]:
            lines.append(f"- {action['action']} -> {action['violating_rows']} violating rows")
    if summary.get("note"):
        lines.append("")
        lines.append(summary["note"])
    return "\n".join(lines)


def _json_default(obj: Any) -> Any:
    """JSON encoder for numpy scalars/arrays produced by groupby aggregations."""
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        return float(obj)
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def save_study_outputs(
    result: HPAStudyResult,
    output_dir: str | Path = BASE_DIR / "data",
    prefix: str = "hpa_transfer",
) -> dict[str, Path]:
    """Persists runs, per-second rows, summary JSON, and the markdown table."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = {
        "runs": out / f"{prefix}_runs.parquet",
        "rows": out / f"{prefix}_rows.parquet",
        "summary": out / f"{prefix}_summary.json",
        "table": out / f"{prefix}_table.md",
    }
    result.runs.to_parquet(paths["runs"], index=False)
    result.rows.to_parquet(paths["rows"], index=False)
    paths["summary"].write_text(json.dumps(result.to_dict(), indent=2, default=_json_default))
    table = format_transfer_table(result) + "\n\n" + format_connection_report(result) + "\n"
    paths["table"].write_text(table)
    return paths
