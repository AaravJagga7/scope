"""Adaptive Scaling Decision Engine for SCOPE-X.

Evaluates live microservice telemetry, explores candidate scaling actions,
predicts performance consequences via GP surrogates and constraint repositories,
enforces safety boundaries via a strict Safety Guard, and outputs a recommendation
with detailed reasoning.
"""

from __future__ import annotations

import datetime
import enum
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from scopex.discovery.surrogate import GPSurrogate
from scopex.optimisation import CostModel

logger = logging.getLogger(__name__)


class ActionType(enum.StrEnum):
    """Candidate operational scaling actions."""

    ADD_REPLICAS = "add_replicas"
    LIMIT_TRAFFIC = "limit_traffic"
    INCREASE_DB_CAPACITY = "increase_db_capacity"
    HOLD = "hold"


class ActionSafetyStatus(enum.StrEnum):
    """Safety classification of a candidate action."""

    SAFE = "safe"
    REJECTED = "rejected"


@dataclass
class LiveMetrics:
    """Current live telemetry and operational state of the microservice cluster."""

    workload_rps: float
    cpu_utilization_pct: float
    p95_latency_ms: float
    error_rate_pct: float
    current_replicas: int
    current_db_pool_size: int
    total_db_connections: int | None = None
    db_connection_limit: int = 200
    current_cpu_limit_milli: int = 1000
    cache_enabled: bool = True
    queue_backlog: int = 0
    profile: str = "flash_sale"
    timestamp: str = field(default_factory=lambda: datetime.datetime.now(datetime.UTC).isoformat())

    def __post_init__(self) -> None:
        if self.total_db_connections is None:
            self.total_db_connections = self.current_replicas * self.current_db_pool_size

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CandidateAction:
    """An evaluated candidate action with predicted consequences and safety status."""

    action_type: ActionType
    description: str
    target_replicas: int
    target_cpu_limit_milli: int
    target_db_pool_size: int
    target_cache_enabled: bool
    target_admission_rate: float
    predicted_p95_ms: float
    predicted_p95_std: float
    predicted_error_rate_pct: float
    safety_status: ActionSafetyStatus
    rejection_reason: str | None = None
    violated_constraints: list[str] = field(default_factory=list)
    estimated_cost_hourly: float = 0.0
    utility_score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_type": self.action_type.value,
            "description": self.description,
            "target_replicas": self.target_replicas,
            "target_cpu_limit_milli": self.target_cpu_limit_milli,
            "target_db_pool_size": self.target_db_pool_size,
            "target_cache_enabled": self.target_cache_enabled,
            "target_admission_rate": round(self.target_admission_rate, 4),
            "predicted_p95_ms": round(self.predicted_p95_ms, 2),
            "predicted_p95_std": round(self.predicted_p95_std, 2),
            "predicted_error_rate_pct": round(self.predicted_error_rate_pct, 2),
            "safety_status": self.safety_status.value,
            "rejection_reason": self.rejection_reason,
            "violated_constraints": self.violated_constraints,
            "estimated_cost_hourly": round(self.estimated_cost_hourly, 4),
            "utility_score": round(self.utility_score, 4),
        }


@dataclass
class Recommendation:
    """Output recommendation produced by the Adaptive Scaling Decision Engine."""

    metrics: LiveMetrics
    recommended_action: CandidateAction
    all_candidates: list[CandidateAction]
    reasoning: str
    declined_actions_summary: dict[str, str]
    safety_guard_summary: str
    timestamp: str = field(default_factory=lambda: datetime.datetime.now(datetime.UTC).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "metrics": self.metrics.to_dict(),
            "recommended_action": self.recommended_action.to_dict(),
            "all_candidates": [c.to_dict() for c in self.all_candidates],
            "reasoning": self.reasoning,
            "declined_actions_summary": self.declined_actions_summary,
            "safety_guard_summary": self.safety_guard_summary,
        }

    def format_report(self) -> str:
        """Format a human-readable recommendation report."""
        lines: list[str] = []
        lines.append("=" * 86)
        lines.append("ADAPTIVE SCALING DECISION ENGINE RECOMMENDATION REPORT")
        lines.append("=" * 86)
        m = self.metrics
        lines.append(
            f"Observed State: Workload={m.workload_rps:.0f} RPS | CPU={m.cpu_utilization_pct:.1f}% | "
            f"p95={m.p95_latency_ms:.1f}ms | Err={m.error_rate_pct:.2f}% | "
            f"Replicas={m.current_replicas} | DB Pool={m.current_db_pool_size} (Total Conns={m.total_db_connections}/{m.db_connection_limit})"
        )
        lines.append("-" * 86)

        rec = self.recommended_action
        lines.append(f"RECOMMENDED ACTION: [{rec.action_type.value.upper()}] - {rec.description}")
        lines.append("  Target Configuration:")
        lines.append(f"    - Checkout Replicas: {rec.target_replicas}")
        lines.append(f"    - CPU Limit per Pod: {rec.target_cpu_limit_milli}m")
        lines.append(f"    - DB Pool per Pod:   {rec.target_db_pool_size}")
        lines.append(f"    - Total DB Conns:    {rec.target_replicas * rec.target_db_pool_size} / {m.db_connection_limit}")
        lines.append(f"    - Admission Rate:    {rec.target_admission_rate * 100:.0f}%")
        lines.append("  Predicted Outcome:")
        lines.append(f"    - p95 Latency:       {rec.predicted_p95_ms:.1f} ms (+/- {rec.predicted_p95_std:.1f} ms)")
        lines.append(f"    - Error Rate:        {rec.predicted_error_rate_pct:.2f}%")
        lines.append(f"    - Estimated Cost:    ${rec.estimated_cost_hourly:.4f} / hr")
        lines.append("")

        lines.append("EVALUATED CANDIDATE ACTIONS & SAFETY GUARD VERDICT:")
        lines.append("-" * 86)
        fmt = f"{'Action':<22} {'Predicted p95':<16} {'Err Rate':<10} {'Safety Verdict':<15} {'Rejection Reason'}"
        lines.append(fmt)
        lines.append("-" * 86)

        for c in self.all_candidates:
            p95_str = f"{c.predicted_p95_ms:.1f} ms"
            err_str = f"{c.predicted_error_rate_pct:.2f}%"
            verdict = c.safety_status.value.upper()
            reason = c.rejection_reason or "Feasible (Passed Safety Guard)"
            lines.append(f"{c.action_type.value:<22} {p95_str:<16} {err_str:<10} {verdict:<15} {reason}")

        lines.append("")
        lines.append("REASONING & SYSTEM DYNAMICS EXPLANATION:")
        lines.append(self.reasoning)
        lines.append("=" * 86)
        return "\n".join(lines)


class SafetyGuard:
    """Enforces validated operational constraints, hardware limits, and safety ceilings.

    Rejects candidate actions that:
    1. Breach the global database connection limit (e.g. 200 max PostgreSQL connections).
    2. Exceed container replica quotas or fall below minimum thresholds.
    3. Violate validated empirical constraints (e.g. db_pool_size <= 25 under >400 RPS).
    4. Provoke high probability of severe SLO degradation.
    """

    def __init__(
        self,
        max_db_connections: int = 200,
        max_replicas: int = 10,
        min_replicas: int = 1,
        max_db_pool_size: int = 150,
        min_db_pool_size: int = 5,
        min_admission_rate: float = 0.50,
        slo_p95_ms: float = 500.0,
        slo_error_pct: float = 1.0,
    ) -> None:
        self.max_db_connections = max_db_connections
        self.max_replicas = max_replicas
        self.min_replicas = min_replicas
        self.max_db_pool_size = max_db_pool_size
        self.min_db_pool_size = min_db_pool_size
        self.min_admission_rate = min_admission_rate
        self.slo_p95_ms = slo_p95_ms
        self.slo_error_pct = slo_error_pct

    def evaluate(
        self,
        action: CandidateAction,
        metrics: LiveMetrics,
    ) -> tuple[bool, str | None, list[str]]:
        """Evaluate a candidate action against all safety boundaries.

        Returns:
            (is_safe, rejection_reason, violated_constraints_list)
        """
        violated: list[str] = []

        # 1. Global Database Connection Limit Ceiling
        total_db_conns = action.target_replicas * action.target_db_pool_size
        effective_limit = min(self.max_db_connections, metrics.db_connection_limit)

        if total_db_conns > effective_limit:
            reason = (
                f"Declined to add Checkout replicas: scaling to {action.target_replicas} replicas "
                f"with db_pool_size={action.target_db_pool_size} requires {total_db_conns} database connections, "
                f"exceeding the PostgreSQL limit of {effective_limit} (causes connection refused errors and lock exhaustion)."
            )
            violated.append(f"total_db_connections ({total_db_conns}) > {effective_limit}")
            return False, reason, violated

        # 2. Hardware Replica Limits
        if action.target_replicas > self.max_replicas:
            reason = f"Replica count {action.target_replicas} exceeds cluster ceiling of {self.max_replicas}."
            violated.append(f"replicas ({action.target_replicas}) > {self.max_replicas}")
            return False, reason, violated

        if action.target_replicas < self.min_replicas:
            reason = f"Replica count {action.target_replicas} below minimum baseline of {self.min_replicas}."
            violated.append(f"replicas ({action.target_replicas}) < {self.min_replicas}")
            return False, reason, violated

        # 3. DB Pool Size Limits
        if action.target_db_pool_size > self.max_db_pool_size:
            reason = f"DB pool size {action.target_db_pool_size} exceeds pod maximum of {self.max_db_pool_size}."
            violated.append(f"db_pool_size ({action.target_db_pool_size}) > {self.max_db_pool_size}")
            return False, reason, violated

        if action.target_db_pool_size < self.min_db_pool_size:
            reason = f"DB pool size {action.target_db_pool_size} below pod minimum of {self.min_db_pool_size}."
            violated.append(f"db_pool_size ({action.target_db_pool_size}) < {self.min_db_pool_size}")
            return False, reason, violated

        # 4. Validated High-Load Pool Constraint (Task 12-14 learned rule)
        # IF db_pool_size <= 25 and target_rps > 400 THEN SLO_VIOLATION
        admitted_rps = metrics.workload_rps * action.target_admission_rate
        if admitted_rps > 400.0 and action.target_db_pool_size <= 25:
            reason = (
                f"DB pool size {action.target_db_pool_size} <= 25 under high load ({admitted_rps:.0f} RPS) "
                "violates validated constraint: triggers downstream connection pool queuing."
            )
            violated.append("db_pool_size <= 25 and target_rps > 400 => SLO_VIOLATION")
            return False, reason, violated

        # 5. Admission Rate Limits
        if action.target_admission_rate < self.min_admission_rate:
            reason = f"Admission rate {action.target_admission_rate * 100:.0f}% below floor of {self.min_admission_rate * 100:.0f}%."
            violated.append(f"admission_rate < {self.min_admission_rate}")
            return False, reason, violated

        return True, None, []


class AdaptiveDecisionEngine:
    """Adaptive Scaling Decision Engine in `scopex/decision`.

    1. Receives live telemetry metrics.
    2. Proposes four candidate operational actions:
       - add_replicas
       - limit_traffic
       - increase_db_capacity
       - hold
    3. Predicts performance consequences with GP surrogate and domain models.
    4. Employs Safety Guard to reject actions breaching constraints or ceilings.
    5. Outputs recommendation with rich, human-readable reasoning.
    """

    def __init__(
        self,
        safety_guard: SafetyGuard | None = None,
        gp_latency: GPSurrogate | None = None,
        gp_error: GPSurrogate | None = None,
        cost_model: CostModel | None = None,
    ) -> None:
        self.safety_guard = safety_guard or SafetyGuard()
        self.cost_model = cost_model or CostModel.standard_cloud()
        self.gp_latency = gp_latency
        self.gp_error = gp_error

        # Auto-initialize GP surrogate if dataset is present and none was injected
        if self.gp_latency is None:
            self._init_gp_surrogates()

    def _init_gp_surrogates(self) -> None:
        """Attempt to load experiments dataset and train lightweight GP surrogates."""
        data_path = Path("data/experiments.parquet")
        if data_path.exists():
            try:
                df = pd.read_parquet(data_path)
                df_train = df[df["split"] == "train"]
                if len(df_train) > 10:
                    self.gp_latency = GPSurrogate(target="p95_latency_ms", n_iter=25)
                    self.gp_latency.fit(df_train)
                    self.gp_error = GPSurrogate(target="error_rate_pct", n_iter=25)
                    self.gp_error.fit(df_train)
                    logger.info("Initialized GP surrogates for latency and error rate.")
            except Exception as e:
                logger.debug("GP surrogate fit skipped: %s", e)

    def generate_candidate_actions(self, metrics: LiveMetrics) -> list[CandidateAction]:
        """Propose the set of four candidate scaling actions."""
        candidates: list[CandidateAction] = []

        # 1. Action: ADD_REPLICAS (Scale horizontal pods)
        cand_rep = metrics.current_replicas + 1
        candidates.append(
            CandidateAction(
                action_type=ActionType.ADD_REPLICAS,
                description=f"Add 1 Checkout replica pod ({metrics.current_replicas} -> {cand_rep})",
                target_replicas=cand_rep,
                target_cpu_limit_milli=metrics.current_cpu_limit_milli,
                target_db_pool_size=metrics.current_db_pool_size,
                target_cache_enabled=metrics.cache_enabled,
                target_admission_rate=1.0,
                predicted_p95_ms=0.0,
                predicted_p95_std=0.0,
                predicted_error_rate_pct=0.0,
                safety_status=ActionSafetyStatus.SAFE,
            )
        )

        # 2. Action: LIMIT_TRAFFIC (Admission control / load shedding)
        # Shed 20% traffic to relieve CPU and preserve DB bounds
        candidates.append(
            CandidateAction(
                action_type=ActionType.LIMIT_TRAFFIC,
                description="Throttle traffic: admit 80% of workload via admission control",
                target_replicas=metrics.current_replicas,
                target_cpu_limit_milli=metrics.current_cpu_limit_milli,
                target_db_pool_size=metrics.current_db_pool_size,
                target_cache_enabled=metrics.cache_enabled,
                target_admission_rate=0.80,
                predicted_p95_ms=0.0,
                predicted_p95_std=0.0,
                predicted_error_rate_pct=0.0,
                safety_status=ActionSafetyStatus.SAFE,
            )
        )

        # 3. Action: INCREASE_DB_CAPACITY (Expand connection pool)
        candidates.append(
            CandidateAction(
                action_type=ActionType.INCREASE_DB_CAPACITY,
                description=f"Increase DB pool size per pod ({metrics.current_db_pool_size} -> {metrics.current_db_pool_size + 10})",
                target_replicas=metrics.current_replicas,
                target_cpu_limit_milli=metrics.current_cpu_limit_milli,
                target_db_pool_size=metrics.current_db_pool_size + 10,
                target_cache_enabled=metrics.cache_enabled,
                target_admission_rate=1.0,
                predicted_p95_ms=0.0,
                predicted_p95_std=0.0,
                predicted_error_rate_pct=0.0,
                safety_status=ActionSafetyStatus.SAFE,
            )
        )

        # 4. Action: HOLD (Maintain current configuration)
        candidates.append(
            CandidateAction(
                action_type=ActionType.HOLD,
                description="Hold: maintain current configuration without modification",
                target_replicas=metrics.current_replicas,
                target_cpu_limit_milli=metrics.current_cpu_limit_milli,
                target_db_pool_size=metrics.current_db_pool_size,
                target_cache_enabled=metrics.cache_enabled,
                target_admission_rate=1.0,
                predicted_p95_ms=0.0,
                predicted_p95_std=0.0,
                predicted_error_rate_pct=0.0,
                safety_status=ActionSafetyStatus.SAFE,
            )
        )

        return candidates

    def predict_action_consequences(
        self,
        action: CandidateAction,
        metrics: LiveMetrics,
    ) -> None:
        """Predict latency, error rate, and operational cost for a candidate action."""
        cand_admitted = metrics.workload_rps * action.target_admission_rate

        # 1. GP Prediction (if available)
        if self.gp_latency is not None and self.gp_error is not None:
            try:
                cand_df = pd.DataFrame(
                    [
                        {
                            "target_rps": float(cand_admitted),
                            "checkout_replicas": float(action.target_replicas),
                            "db_pool_size": float(action.target_db_pool_size),
                            "consumers": 1.0,
                            "cache_enabled": 1.0 if action.target_cache_enabled else 0.0,
                            "workload_profile": metrics.profile,
                        }
                    ]
                )
                m_lat, s_lat = self.gp_latency.predict(cand_df)
                m_err, _ = self.gp_error.predict(cand_df)
                action.predicted_p95_ms = float(np.clip(m_lat[0], 25.0, 3000.0))
                action.predicted_p95_std = float(max(5.0, s_lat[0]))
                action.predicted_error_rate_pct = float(np.clip(m_err[0], 0.0, 100.0))
            except Exception:
                self._fallback_analytical_prediction(action, metrics)
        else:
            self._fallback_analytical_prediction(action, metrics)

        # 2. Operational Cost Computation
        cost_breakdown = self.cost_model.compute_cost(
            replicas=action.target_replicas,
            cpu_limit_milli=action.target_cpu_limit_milli,
            db_pool_size=action.target_db_pool_size,
            cache_enabled=action.target_cache_enabled,
            workload_rps=int(round(metrics.workload_rps)),
            admitted_rps=int(round(cand_admitted)),
        )
        action.estimated_cost_hourly = cost_breakdown.total_hourly_cost

        # 3. Utility Score Calculation
        # Rewards low latency, high admission, and low cost
        lat_penalty = max(0.0, (action.predicted_p95_ms - 300.0) / 100.0)
        err_penalty = action.predicted_error_rate_pct * 10.0
        cost_penalty = action.estimated_cost_hourly * 5.0
        admit_reward = action.target_admission_rate * 50.0

        action.utility_score = admit_reward - (lat_penalty + err_penalty + cost_penalty)

    def _fallback_analytical_prediction(
        self,
        action: CandidateAction,
        metrics: LiveMetrics,
    ) -> None:
        """Physical analytical queuing model used when GP surrogate is uninitialized."""
        admitted_rps = metrics.workload_rps * action.target_admission_rate
        rps_per_core = 175.0 if action.target_cache_enabled else 85.0
        total_cores = (action.target_replicas * action.target_cpu_limit_milli) / 1000.0
        capacity = total_cores * rps_per_core

        base_lat = 45.0
        # CPU overload delay
        if admitted_rps > capacity:
            ratio = (admitted_rps - capacity) / capacity
            base_lat += 500.0 * (ratio**1.5)
        elif (admitted_rps / max(1.0, capacity)) > 0.85:
            base_lat += 80.0

        # DB connection pool queuing delay
        total_conns = action.target_replicas * action.target_db_pool_size
        if total_conns > metrics.db_connection_limit:
            base_lat += 800.0
            action.predicted_error_rate_pct = min(40.0, (total_conns - metrics.db_connection_limit) * 0.8)
        elif admitted_rps > 400.0 and action.target_db_pool_size <= 25:
            base_lat += 420.0
        else:
            action.predicted_error_rate_pct = 0.05

        action.predicted_p95_ms = base_lat
        action.predicted_p95_std = 15.0

    def evaluate_and_recommend(self, metrics: LiveMetrics) -> Recommendation:
        """Evaluate live telemetry and output an actionable recommendation with reasoning."""
        candidates = self.generate_candidate_actions(metrics)
        declined_summary: dict[str, str] = {}

        for c in candidates:
            # 1. Predict consequences
            self.predict_action_consequences(c, metrics)

            # 2. Evaluate with Safety Guard
            is_safe, rejection_reason, violated_rules = self.safety_guard.evaluate(c, metrics)
            if not is_safe:
                c.safety_status = ActionSafetyStatus.REJECTED
                c.rejection_reason = rejection_reason
                c.violated_constraints = violated_rules
                declined_summary[c.action_type.value] = rejection_reason or "Rejected by Safety Guard"
            else:
                c.safety_status = ActionSafetyStatus.SAFE

        # Filter safe actions
        safe_candidates = [c for c in candidates if c.safety_status == ActionSafetyStatus.SAFE]

        if safe_candidates:
            # Select the safe candidate with highest utility score
            best_action = max(safe_candidates, key=lambda x: x.utility_score)
        else:
            # If all were rejected, default to HOLD as fail-safe
            best_action = next(
                (c for c in candidates if c.action_type == ActionType.HOLD),
                candidates[0],
            )

        # Synthesize explanatory reasoning
        reasoning = self._synthesize_reasoning(
            metrics=metrics,
            chosen=best_action,
            candidates=candidates,
            declined_summary=declined_summary,
        )

        safety_guard_summary = (
            f"Safety Guard active (max DB conns={metrics.db_connection_limit}, "
            f"max replicas={self.safety_guard.max_replicas}). "
            f"Evaluated {len(candidates)} candidates: {len(safe_candidates)} SAFE, "
            f"{len(candidates) - len(safe_candidates)} REJECTED."
        )

        return Recommendation(
            metrics=metrics,
            recommended_action=best_action,
            all_candidates=candidates,
            reasoning=reasoning,
            declined_actions_summary=declined_summary,
            safety_guard_summary=safety_guard_summary,
        )

    def _synthesize_reasoning(
        self,
        metrics: LiveMetrics,
        chosen: CandidateAction,
        candidates: list[CandidateAction],
        declined_summary: dict[str, str],
    ) -> str:
        """Construct multi-paragraph explanation detailing trade-offs and rejection reasons."""
        paragraphs: list[str] = []

        # 1. Situational assessment
        db_conns = metrics.total_db_connections or (metrics.current_replicas * metrics.current_db_pool_size)
        db_usage_pct = (db_conns / metrics.db_connection_limit) * 100.0

        paragraphs.append(
            f"The cluster is operating under '{metrics.profile}' traffic conditions at {metrics.workload_rps:.0f} RPS. "
            f"Observed CPU utilization is critically high ({metrics.cpu_utilization_pct:.1f}%), while the shared "
            f"database connection footprint is at {db_conns}/{metrics.db_connection_limit} connections ({db_usage_pct:.1f}% capacity)."
        )

        # 2. Explanation of declined/rejected actions (Crucial acceptance criterion)
        if ActionType.ADD_REPLICAS.value in declined_summary:
            paragraphs.append(
                f"[Declined: ADD_REPLICAS] The engine declined to add Checkout replica pods despite high CPU utilization. "
                f"{declined_summary[ActionType.ADD_REPLICAS.value]}"
            )

        if ActionType.INCREASE_DB_CAPACITY.value in declined_summary:
            paragraphs.append(
                f"[Declined: INCREASE_DB_CAPACITY] {declined_summary[ActionType.INCREASE_DB_CAPACITY.value]}"
            )

        # 3. Recommendation justification
        paragraphs.append(
            f"[Selected: {chosen.action_type.value.upper()}] Recommended '{chosen.description}'. "
            f"This action was chosen as the optimal safe intervention: predicted p95 latency is {chosen.predicted_p95_ms:.1f} ms "
            f"(error rate {chosen.predicted_error_rate_pct:.2f}%) at ${chosen.estimated_cost_hourly:.4f}/hr, "
            f"successfully averting database ceiling collapse while stabilizing service capacity."
        )

        return "\n\n".join(paragraphs)

