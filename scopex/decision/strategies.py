"""Autonomous autoscaling and resource allocation strategies for SCOPE-X comparative evaluation."""

from __future__ import annotations

import abc
import math
from dataclasses import dataclass
from typing import Any

from scopex.optimisation import CostModel, CPSATConfigOptimizer


@dataclass
class StrategyDecision:
    """Resource configuration decided by a scaling strategy for a time step."""

    replicas: int
    cpu_limit_milli: int
    db_pool_size: int
    cache_enabled: bool
    admission_rate: float  # [0.0, 1.0]

    def to_dict(self) -> dict[str, Any]:
        return {
            "replicas": self.replicas,
            "cpu_limit_milli": self.cpu_limit_milli,
            "db_pool_size": self.db_pool_size,
            "cache_enabled": self.cache_enabled,
            "admission_rate": round(self.admission_rate, 4),
        }


@dataclass
class SystemState:
    """Current observable telemetry state of the microservice cluster."""

    time_step: int
    workload_rps: float
    current_replicas: int
    cpu_utilization_pct: float
    queue_backlog: int
    p95_latency_ms: float
    error_rate_pct: float
    db_connections_active: int


class ScalingStrategy(abc.ABC):
    """Abstract base class for infrastructure autoscaling and control strategies."""

    name: str
    description: str

    @abc.abstractmethod
    def decide(self, state: SystemState) -> StrategyDecision:
        """Evaluate current system telemetry and return resource allocations."""
        raise NotImplementedError

    def reset(self) -> None:
        """Reset internal state (buffers, timers, smoothing filters). Default is a no-op."""
        return


class FixedStrategy(ScalingStrategy):
    """Baseline: Static fixed over/under-provisioned configuration without autoscaling.

    Maintains constant replica count, CPU limit, and database pool size regardless
    of incoming traffic surges. Under flash sales, traffic severely exceeds capacity.
    """

    def __init__(
        self,
        replicas: int = 2,
        cpu_limit_milli: int = 1000,
        db_pool_size: int = 20,
        cache_enabled: bool = True,
    ) -> None:
        self.name = "Fixed Baseline"
        self.description = f"Static configuration (replicas={replicas}, cpu={cpu_limit_milli}m, pool={db_pool_size})"
        self.replicas = replicas
        self.cpu_limit_milli = cpu_limit_milli
        self.db_pool_size = db_pool_size
        self.cache_enabled = cache_enabled

    def decide(self, state: SystemState) -> StrategyDecision:
        return StrategyDecision(
            replicas=self.replicas,
            cpu_limit_milli=self.cpu_limit_milli,
            db_pool_size=self.db_pool_size,
            cache_enabled=self.cache_enabled,
            admission_rate=1.0,
        )


class KubernetesHPAStrategy(ScalingStrategy):
    """Standard Kubernetes Horizontal Pod Autoscaler (HPA) using CPU utilization.

    Scales replicas reactively based on a target CPU threshold (e.g. 70%):
        desired_replicas = ceil(current_replicas * (current_cpu / target_cpu))

    Key Limitations:
    1. Reactive scaling delay: Pod scheduling and container startup impose a 10-15s lag.
    2. Missing holistic tuning: Leaves DB pool size static, causing downstream DB starvation
       when frontend replicas scale up.
    3. No traffic admission control: Adopts 100% of traffic even when overloaded.
    """

    def __init__(
        self,
        target_cpu_pct: float = 70.0,
        min_replicas: int = 2,
        max_replicas: int = 10,
        cpu_limit_milli: int = 1000,
        static_db_pool_size: int = 20,
        cooldown_steps: int = 3,  # Scaling delay / stabilization window
    ) -> None:
        self.name = "Kubernetes HPA (CPU)"
        self.description = f"Reactive CPU autoscaler (target={target_cpu_pct}%, static pool={static_db_pool_size})"
        self.target_cpu_pct = target_cpu_pct
        self.min_replicas = min_replicas
        self.max_replicas = max_replicas
        self.cpu_limit_milli = cpu_limit_milli
        self.static_db_pool_size = static_db_pool_size
        self.cooldown_steps = cooldown_steps

        self._current_replicas = min_replicas
        self._last_scale_step = -999

    def reset(self) -> None:
        self._current_replicas = self.min_replicas
        self._last_scale_step = -999

    def decide(self, state: SystemState) -> StrategyDecision:
        # Standard Kubernetes HPA algorithm
        observed_cpu = max(10.0, state.cpu_utilization_pct)
        ratio = observed_cpu / self.target_cpu_pct

        # Apply HPA tolerance (Kubernetes uses 10% deadband: 0.9 to 1.1)
        if ratio > 1.10 and (state.time_step - self._last_scale_step >= self.cooldown_steps):
            desired = math.ceil(state.current_replicas * ratio)
            desired = max(self.min_replicas, min(self.max_replicas, desired))
            if desired > self._current_replicas:
                self._current_replicas = desired
                self._last_scale_step = state.time_step
        elif ratio < 0.85 and (state.time_step - self._last_scale_step >= self.cooldown_steps * 2):
            desired = math.floor(state.current_replicas * ratio)
            desired = max(self.min_replicas, min(self.max_replicas, desired))
            if desired < self._current_replicas:
                self._current_replicas = desired
                self._last_scale_step = state.time_step

        return StrategyDecision(
            replicas=self._current_replicas,
            cpu_limit_milli=self.cpu_limit_milli,
            db_pool_size=self.static_db_pool_size,  # HPA does NOT tune DB pools!
            cache_enabled=True,
            admission_rate=1.0,
        )


class QueueBacklogStrategy(ScalingStrategy):
    """Message Queue backlog / queue-depth-based autoscaler (KEDA-style).

    Monitors pending order messages in the asynchronous queue and scales
    replicas proportionally to the queue depth:
        desired_replicas = ceil(backlog / target_backlog_per_pod)

    Key Limitations:
    1. Lag until backlog accumulates: Orders must queue before scaling initiates.
    2. Queue flapping: Rapid scale-down once backlog drains, re-accumulating backlog.
    3. Ignores database connection limits and caching requirements.
    """

    def __init__(
        self,
        target_backlog_per_pod: int = 50,
        min_replicas: int = 2,
        max_replicas: int = 10,
        cpu_limit_milli: int = 1000,
        static_db_pool_size: int = 20,
        cooldown_steps: int = 2,
    ) -> None:
        self.name = "Queue-Backlog Scaler"
        self.description = f"Queue-driven autoscaler (target={target_backlog_per_pod} msgs/pod)"
        self.target_backlog_per_pod = target_backlog_per_pod
        self.min_replicas = min_replicas
        self.max_replicas = max_replicas
        self.cpu_limit_milli = cpu_limit_milli
        self.static_db_pool_size = static_db_pool_size
        self.cooldown_steps = cooldown_steps

        self._current_replicas = min_replicas
        self._last_scale_step = -999

    def reset(self) -> None:
        self._current_replicas = self.min_replicas
        self._last_scale_step = -999

    def decide(self, state: SystemState) -> StrategyDecision:
        if state.queue_backlog > 10 and (state.time_step - self._last_scale_step >= self.cooldown_steps):
            desired = math.ceil(state.queue_backlog / float(self.target_backlog_per_pod))
            desired = max(self.min_replicas, min(self.max_replicas, desired))
            if desired != self._current_replicas:
                self._current_replicas = desired
                self._last_scale_step = state.time_step
        elif state.queue_backlog <= 5 and (state.time_step - self._last_scale_step >= self.cooldown_steps * 2):
            self._current_replicas = max(self.min_replicas, self._current_replicas - 1)
            self._last_scale_step = state.time_step

        return StrategyDecision(
            replicas=self._current_replicas,
            cpu_limit_milli=self.cpu_limit_milli,
            db_pool_size=self.static_db_pool_size,
            cache_enabled=True,
            admission_rate=1.0,
        )


class ScopeXStrategy(ScalingStrategy):
    """SCOPE-X: Constraint-guided, cost-optimal multi-dimensional optimizer.

    Solves an exact OR-Tools CP-SAT formulation at each evaluation window:
    - Jointly decides Replicas, Container CPU limit, DB Connection Pool size,
      Cache enablement, and Admission rate.
    - Proactively prevents downstream bottleneck shifting (e.g. tightening DB pool
      under high concurrency).
    - Protects p95 latency <= 500ms and error rate <= 1.0% at minimum cost.
    """

    def __init__(
        self,
        cost_model: CostModel | None = None,
        max_replicas: int = 10,
        max_cpu_milli: int = 2000,
    ) -> None:
        self.name = "SCOPE-X (Ours)"
        self.description = "Constraint-guided, cost-optimal CP-SAT co-optimization"
        self.optimizer = CPSATConfigOptimizer(
            cost_model=cost_model or CostModel.standard_cloud(),
            max_replicas=max_replicas,
            max_cpu_milli=max_cpu_milli,
        )

    def decide(self, state: SystemState) -> StrategyDecision:
        # Round observed workload into positive integer RPS
        workload_int = max(10, int(round(state.workload_rps)))

        opt_result = self.optimizer.optimize(
            workload_rps=workload_int,
            min_admission_rate=0.5,  # Allow graceful load shedding only if cluster is overwhelmed
        )
        c = opt_result.cheapest_config

        return StrategyDecision(
            replicas=c.checkout_replicas,
            cpu_limit_milli=c.cpu_limit_milli,
            db_pool_size=c.db_pool_size,
            cache_enabled=c.cache_enabled,
            admission_rate=c.admission_rate,
        )
