"""OR-Tools CP-SAT configuration optimizer for microservice resources and cost minimization."""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from typing import Any

from ortools.sat.python import cp_model

from scopex.optimisation.cost_model import CostBreakdown, CostModel

logger = logging.getLogger(__name__)


@dataclass
class ActiveConstraint:
    """An operational or system constraint evaluated at the optimal solution.

    Attributes:
        name: Name of the constraint.
        category: Category (e.g. 'capacity', 'database', 'caching', 'admission', 'resource_bound').
        description: Human-readable explanation of how this constraint affected the solution.
        inequality: Formal inequality representing the constraint.
        bound_value: Numeric bound value.
        actual_value: Numeric actual value attained at the optimal solution.
        slack: Distance from the boundary (slack = 0 indicates tightly binding).
        is_binding: True if this constraint is actively limiting or driving the optimal solution.
    """

    name: str
    category: str
    description: str
    inequality: str
    bound_value: float
    actual_value: float
    slack: float
    is_binding: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "category": self.category,
            "description": self.description,
            "inequality": self.inequality,
            "bound_value": round(self.bound_value, 2),
            "actual_value": round(self.actual_value, 2),
            "slack": round(self.slack, 2),
            "is_binding": self.is_binding,
        }


@dataclass
class CheapestConfig:
    """The cheapest feasible microservice deployment configuration."""

    checkout_replicas: int
    db_pool_size: int
    cpu_limit: str
    cpu_limit_milli: int
    admission_rate: float
    admission_rate_pct: int
    admitted_rps: int
    shed_rps: int
    cache_enabled: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "checkout_replicas": self.checkout_replicas,
            "db_pool_size": self.db_pool_size,
            "cpu_limit": self.cpu_limit,
            "cpu_limit_milli": self.cpu_limit_milli,
            "admission_rate": round(self.admission_rate, 4),
            "admission_rate_pct": self.admission_rate_pct,
            "admitted_rps": self.admitted_rps,
            "shed_rps": self.shed_rps,
            "cache_enabled": self.cache_enabled,
        }


@dataclass
class OptimizationResult:
    """Complete summary of the CP-SAT configuration optimization."""

    workload_rps: int
    status: str
    cheapest_config: CheapestConfig
    cost_breakdown: CostBreakdown
    active_constraints: list[ActiveConstraint]
    explanation: str
    solve_time_ms: float
    slo_p95_latency_ms: float = 500.0
    slo_error_rate_pct: float = 1.0

    @property
    def binding_constraints(self) -> list[ActiveConstraint]:
        """Return only constraints that are tightly binding at the solution."""
        return [c for c in self.active_constraints if c.is_binding]

    def to_dict(self) -> dict[str, Any]:
        return {
            "workload_rps": self.workload_rps,
            "status": self.status,
            "cheapest_config": self.cheapest_config.to_dict(),
            "cost_breakdown": self.cost_breakdown.to_dict(),
            "active_constraints": [c.to_dict() for c in self.active_constraints],
            "binding_constraints": [c.to_dict() for c in self.binding_constraints],
            "explanation": self.explanation,
            "solve_time_ms": round(self.solve_time_ms, 2),
            "slo_p95_latency_ms": self.slo_p95_latency_ms,
            "slo_error_rate_pct": self.slo_error_rate_pct,
        }

    def format_report(self) -> str:
        """Format a human-readable optimization summary report."""
        lines: list[str] = []
        lines.append("=" * 86)
        lines.append(f"CP-SAT CONFIGURATION OPTIMIZATION REPORT (Workload: {self.workload_rps} RPS)")
        lines.append("=" * 86)
        lines.append(f"Solver Status: {self.status}  |  Solve Time: {self.solve_time_ms:.2f} ms")
        lines.append(f"SLO Target: p95 latency <= {self.slo_p95_latency_ms:.0f} ms, error rate <= {self.slo_error_rate_pct:.1f}%")
        lines.append("-" * 86)

        c = self.cheapest_config
        lines.append("1. CHEAPEST FEASIBLE CONFIGURATION:")
        lines.append(f"   - Replicas (Checkout):  {c.checkout_replicas}")
        lines.append(f"   - CPU Limit per Pod:    {c.cpu_limit} ({c.cpu_limit_milli} milliCPUs)")
        lines.append(f"   - DB Connection Pool:   {c.db_pool_size} connections")
        lines.append(f"   - Cache Enabled:        {c.cache_enabled}")
        lines.append(f"   - Admission Rate:       {c.admission_rate_pct}% ({c.admitted_rps} / {self.workload_rps} RPS admitted, {c.shed_rps} RPS shed)")
        lines.append("")

        b = self.cost_breakdown
        lines.append("2. ESTIMATED COST BREAKDOWN ($/hour):")
        lines.append(f"   - Replica Pod Base Cost: ${b.replica_base_cost:.4f} / hr")
        lines.append(f"   - CPU Compute Cost:      ${b.cpu_cost:.4f} / hr")
        lines.append(f"   - DB Connection Cost:    ${b.db_pool_cost:.4f} / hr")
        lines.append(f"   - Caching Layer Cost:    ${b.cache_cost:.4f} / hr")
        lines.append(f"   - Load Shedding Penalty: ${b.penalty_cost:.4f} / hr")
        lines.append("   ----------------------------------------")
        lines.append(f"   * TOTAL ESTIMATED COST:  ${b.total_hourly_cost:.4f} / hour")
        lines.append("")

        lines.append("3. ACTIVE & BINDING CONSTRAINTS EXPLANATION:")
        lines.append("-" * 86)
        fmt = f"{'Constraint Name':<28} {'Bound':<12} {'Actual':<12} {'Slack':<10} {'Binding?'}"
        lines.append(fmt)
        lines.append("-" * 86)

        for ac in self.active_constraints:
            binding_str = "YES" if ac.is_binding else "No"
            lines.append(f"{ac.name:<28} {ac.bound_value:<12.1f} {ac.actual_value:<12.1f} {ac.slack:<10.1f} {binding_str}")
            if ac.is_binding:
                lines.append(f"   >> {ac.description}")

        lines.append("")
        lines.append("4. SYNTHESIS EXPLANATION:")
        lines.append(f"   {self.explanation}")
        lines.append("=" * 86)
        return "\n".join(lines)


class CPSATConfigOptimizer:
    """Uses Google OR-Tools CP-SAT to select cost-optimal microservice configurations.

    Solves an exact Constraint Programming satisfaction and optimization model
    that minimises total operational cost while strictly adhering to:
    - Service CPU throughput capacity boundaries.
    - Validated caching thresholds (from empirical discovery).
    - Database connection pool concurrency floors.
    - Global PostgreSQL connection limits.
    - Configured admission control and load shedding policies.

    Args:
        cost_model: Configurable infrastructure and operational cost model.
        min_replicas: Minimum allowed replica pods (default: 1).
        max_replicas: Maximum allowed replica pods (default: 20).
        min_db_pool: Minimum database connections per pod (default: 5).
        max_db_pool: Maximum database connections per pod (default: 150).
        min_cpu_milli: Minimum CPU limit in milliCPUs (default: 250).
        max_cpu_milli: Maximum CPU limit in milliCPUs (default: 4000).
        cpu_step_milli: Granularity of CPU limit options (default: 250).
        max_db_connections: Global PostgreSQL database connection ceiling (default: 200).
        rps_per_core_cached: Throughput capacity per 1000m CPU with cache (default: 175).
        rps_per_core_uncached: Throughput capacity per 1000m CPU without cache (default: 85).
        cache_forced_threshold_rps: Traffic threshold above which cache is required (default: 300).
        concurrency_per_pool_conn: Admitted RPS supported per DB pool connection (default: 20).
        high_load_threshold_rps: High traffic threshold for tightened DB pool (default: 400).
        high_load_min_pool: Minimum DB pool size under high load (default: 25).
        slo_p95_latency_ms: Target p95 latency SLO threshold in ms (default: 500.0).
        slo_error_rate_pct: Target maximum error rate percentage (default: 1.0).
    """

    def __init__(
        self,
        cost_model: CostModel | None = None,
        min_replicas: int = 1,
        max_replicas: int = 20,
        min_db_pool: int = 5,
        max_db_pool: int = 150,
        min_cpu_milli: int = 250,
        max_cpu_milli: int = 2000,
        cpu_step_milli: int = 250,
        max_db_connections: int = 200,
        rps_per_core_cached: int = 175,
        rps_per_core_uncached: int = 85,
        cache_forced_threshold_rps: int = 300,
        concurrency_per_pool_conn: int = 20,
        high_load_threshold_rps: int = 400,
        high_load_min_pool: int = 25,
        slo_p95_latency_ms: float = 500.0,
        slo_error_rate_pct: float = 1.0,
    ) -> None:
        self.cost_model = cost_model or CostModel()
        self.min_replicas = min_replicas
        self.max_replicas = max_replicas
        self.min_db_pool = min_db_pool
        self.max_db_pool = max_db_pool
        self.min_cpu_milli = min_cpu_milli
        self.max_cpu_milli = max_cpu_milli
        self.cpu_step_milli = cpu_step_milli
        self.max_db_connections = max_db_connections
        self.rps_per_core_cached = rps_per_core_cached
        self.rps_per_core_uncached = rps_per_core_uncached
        self.cache_forced_threshold_rps = cache_forced_threshold_rps
        self.concurrency_per_pool_conn = concurrency_per_pool_conn
        self.high_load_threshold_rps = high_load_threshold_rps
        self.high_load_min_pool = high_load_min_pool
        self.slo_p95_latency_ms = slo_p95_latency_ms
        self.slo_error_rate_pct = slo_error_rate_pct

    def optimize(
        self,
        workload_rps: int,
        min_admission_rate: float = 0.0,
        force_cache: bool | None = None,
        time_limit_seconds: float = 10.0,
    ) -> OptimizationResult:
        """Find the cost-optimal configuration for the given workload.

        Args:
            workload_rps: Target incoming workload level in requests per second.
            min_admission_rate: Minimum acceptable admission rate in [0.0, 1.0] (default: 0.0).
            force_cache: Optional boolean override to fix cache enabled/disabled.
            time_limit_seconds: Solver maximum time limit in seconds (default: 10.0).

        Returns:
            :class:`OptimizationResult` containing optimal configuration, cost breakdown,
            active constraints, and human-readable explanation.
        """
        if workload_rps <= 0:
            raise ValueError(f"workload_rps must be positive, got {workload_rps}")
        if not (0.0 <= min_admission_rate <= 1.0):
            raise ValueError(f"min_admission_rate must be in [0.0, 1.0], got {min_admission_rate}")

        start_time = time.perf_counter()
        model = cp_model.CpModel()

        # -------------------------------------------------------------
        # Decision Variables
        # -------------------------------------------------------------
        # 1. Replicas: integer in [min_replicas, max_replicas]
        replicas_var = model.NewIntVar(
            self.min_replicas, self.max_replicas, "replicas"
        )

        # 2. Database Connection Pool: integer in [min_db_pool, max_db_pool]
        db_pool_var = model.NewIntVar(
            self.min_db_pool, self.max_db_pool, "db_pool_size"
        )

        # 3. CPU Limit in discrete steps (e.g. 250m, 500m, 750m, 1000m, ...)
        cpu_min_units = self.min_cpu_milli // self.cpu_step_milli
        cpu_max_units = self.max_cpu_milli // self.cpu_step_milli
        cpu_units_var = model.NewIntVar(cpu_min_units, cpu_max_units, "cpu_units")

        cpu_milli_var = model.NewIntVar(
            self.min_cpu_milli, self.max_cpu_milli, "cpu_limit_milli"
        )
        model.Add(cpu_milli_var == cpu_units_var * self.cpu_step_milli)

        # 4. Admission rate (percentage 0 to 100) and admitted RPS
        min_pct = int(min_admission_rate * 100)
        admission_pct_var = model.NewIntVar(min_pct, 100, "admission_rate_pct")
        admitted_rps_var = model.NewIntVar(0, workload_rps, "admitted_rps")
        shed_rps_var = model.NewIntVar(0, workload_rps, "shed_rps")

        # admitted_rps * 100 <= workload_rps * admission_pct
        model.Add(admitted_rps_var * 100 <= workload_rps * admission_pct_var)
        model.Add(admitted_rps_var * 100 + 100 > workload_rps * admission_pct_var)
        model.Add(shed_rps_var == workload_rps - admitted_rps_var)

        # 5. Cache layer enabled boolean
        cache_var = model.NewBoolVar("cache_enabled")
        if force_cache is not None:
            model.Add(cache_var == (1 if force_cache else 0))

        # -------------------------------------------------------------
        # Constraints & Bilinear Relations
        # -------------------------------------------------------------
        # 1. Total CPU Capacity: prod_cpu = replicas * cpu_limit_milli
        prod_cpu = model.NewIntVar(
            self.min_replicas * self.min_cpu_milli,
            self.max_replicas * self.max_cpu_milli,
            "prod_cpu",
        )
        model.AddMultiplicationEquality(prod_cpu, [replicas_var, cpu_milli_var])

        # Throughput Capacity Constraint:
        # Total capacity = (prod_cpu / 1000) * rps_per_core
        # Linearized: 1000 * admitted_rps <= rps_per_core * prod_cpu
        model.Add(
            1000 * admitted_rps_var <= self.rps_per_core_cached * prod_cpu
        ).OnlyEnforceIf(cache_var)

        model.Add(
            1000 * admitted_rps_var <= self.rps_per_core_uncached * prod_cpu
        ).OnlyEnforceIf(cache_var.Not())

        # 2. Validated Cache Constraint (from Task 12-14 discovery):
        # IF cache_enabled <= 0.5 and target_rps > 300 THEN SLO_VIOLATION
        # If cache is disabled, admitted traffic must not exceed 300 RPS
        model.Add(
            admitted_rps_var <= self.cache_forced_threshold_rps
        ).OnlyEnforceIf(cache_var.Not())

        # 3. Global Database Connection Limit & Concurrency:
        # Total cluster connections: replicas * db_pool_size <= max_db_connections
        prod_db = model.NewIntVar(
            self.min_replicas * self.min_db_pool,
            self.max_replicas * self.max_db_pool,
            "prod_db",
        )
        model.AddMultiplicationEquality(prod_db, [replicas_var, db_pool_var])
        model.Add(prod_db <= self.max_db_connections)

        # Cluster concurrency floor: Need at least 1 total DB connection per concurrency_per_pool_conn (e.g. 20 RPS)
        model.Add(self.concurrency_per_pool_conn * prod_db >= admitted_rps_var)

        # 4. Validated High-Load DB Constraint (Task 12-14 discovery):
        # IF target_rps > 400 THEN db_pool_size >= 25
        is_high_load = model.NewBoolVar("is_high_load")
        model.Add(admitted_rps_var > self.high_load_threshold_rps).OnlyEnforceIf(
            is_high_load
        )
        model.Add(admitted_rps_var <= self.high_load_threshold_rps).OnlyEnforceIf(
            is_high_load.Not()
        )
        model.Add(db_pool_var >= self.high_load_min_pool).OnlyEnforceIf(
            is_high_load
        )

        # -------------------------------------------------------------
        # Objective Function: Minimize Total Operational Cost
        # -------------------------------------------------------------
        # Scale monetary costs into integer units ($0.0001 precision)
        scale = self.cost_model.scale_factor
        c_rep = int(round(scale * self.cost_model.replica_base_cost_hourly))
        # cpu cost per milliCPU = cpu_core_cost / 1000
        c_cpu_milli = int(
            round(scale * (self.cost_model.cpu_core_cost_hourly / 1000.0))
        )
        c_db = int(round(scale * self.cost_model.db_conn_cost_hourly))
        c_cache = int(round(scale * self.cost_model.cache_cost_hourly))
        c_shed = int(round(scale * self.cost_model.dropped_rps_penalty_hourly))

        objective_terms = [
            c_rep * replicas_var,
            c_cpu_milli * prod_cpu,
            c_db * prod_db,
            c_cache * cache_var,
            c_shed * shed_rps_var,
        ]
        model.Minimize(cp_model.LinearExpr.Sum(objective_terms))

        # -------------------------------------------------------------
        # Solve
        # -------------------------------------------------------------
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = time_limit_seconds
        solver.parameters.num_workers = 1  # Deterministic execution
        solver_status_code = solver.Solve(model)
        elapsed_ms = (time.perf_counter() - start_time) * 1000.0

        status_name = solver.StatusName(solver_status_code)
        if solver_status_code not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            raise RuntimeError(
                f"CP-SAT failed to find a feasible configuration (status: {status_name}). "
                f"Workload of {workload_rps} RPS may exceed cluster constraints."
            )

        # -------------------------------------------------------------
        # Extract Solution
        # -------------------------------------------------------------
        opt_replicas = int(solver.Value(replicas_var))
        opt_cpu_milli = int(solver.Value(cpu_milli_var))
        opt_db_pool = int(solver.Value(db_pool_var))
        opt_cache = bool(solver.Value(cache_var) == 1)
        opt_admission_pct = int(solver.Value(admission_pct_var))
        opt_admitted_rps = int(solver.Value(admitted_rps_var))
        opt_shed_rps = int(solver.Value(shed_rps_var))

        cost_breakdown = self.cost_model.compute_cost(
            replicas=opt_replicas,
            cpu_limit_milli=opt_cpu_milli,
            db_pool_size=opt_db_pool,
            cache_enabled=opt_cache,
            workload_rps=workload_rps,
            admitted_rps=opt_admitted_rps,
        )

        cheapest_config = CheapestConfig(
            checkout_replicas=opt_replicas,
            db_pool_size=opt_db_pool,
            cpu_limit=f"{opt_cpu_milli}m",
            cpu_limit_milli=opt_cpu_milli,
            admission_rate=opt_admission_pct / 100.0,
            admission_rate_pct=opt_admission_pct,
            admitted_rps=opt_admitted_rps,
            shed_rps=opt_shed_rps,
            cache_enabled=opt_cache,
        )

        # -------------------------------------------------------------
        # Active Constraints Analysis
        # -------------------------------------------------------------
        active_constraints = self._evaluate_active_constraints(
            workload_rps=workload_rps,
            config=cheapest_config,
        )

        explanation = self._generate_synthesis_explanation(
            workload_rps=workload_rps,
            config=cheapest_config,
            cost=cost_breakdown,
            active_constraints=active_constraints,
        )

        return OptimizationResult(
            workload_rps=workload_rps,
            status=status_name,
            cheapest_config=cheapest_config,
            cost_breakdown=cost_breakdown,
            active_constraints=active_constraints,
            explanation=explanation,
            solve_time_ms=elapsed_ms,
            slo_p95_latency_ms=self.slo_p95_latency_ms,
            slo_error_rate_pct=self.slo_error_rate_pct,
        )

    def _evaluate_active_constraints(
        self,
        workload_rps: int,
        config: CheapestConfig,
    ) -> list[ActiveConstraint]:
        """Inspect the solution and evaluate all constraints for tightness/slack."""
        constraints: list[ActiveConstraint] = []

        # 1. Admission Control / Load Shedding
        is_full_admit = config.admission_rate_pct == 100
        constraints.append(
            ActiveConstraint(
                name="Admission Control Upper Bound",
                category="admission",
                description=(
                    f"Full demand served: 100% of incoming workload ({workload_rps} RPS) admitted without load shedding."
                    if is_full_admit
                    else f"Load shedding active: {config.shed_rps} RPS ({100 - config.admission_rate_pct}%) shed to respect resource/cost limits."
                ),
                inequality="admission_rate <= 100%",
                bound_value=100.0,
                actual_value=float(config.admission_rate_pct),
                slack=float(100 - config.admission_rate_pct),
                is_binding=True,
            )
        )

        # 2. Service Throughput Capacity
        rps_per_core = (
            self.rps_per_core_cached
            if config.cache_enabled
            else self.rps_per_core_uncached
        )
        total_cores = (config.checkout_replicas * config.cpu_limit_milli) / 1000.0
        max_service_capacity = total_cores * rps_per_core
        capacity_slack = max_service_capacity - config.admitted_rps
        is_capacity_binding = capacity_slack <= 25.0 or (
            capacity_slack / max(1.0, max_service_capacity) < 0.10
        )

        constraints.append(
            ActiveConstraint(
                name="Service CPU Throughput Capacity",
                category="capacity",
                description=(
                    f"Tight throughput limit: Cluster capacity ({max_service_capacity:.0f} RPS) directly bounds admitted traffic ({config.admitted_rps} RPS). "
                    f"Lowering CPU ({config.cpu_limit}) or replicas ({config.checkout_replicas}) would violate the p95 <= {self.slo_p95_latency_ms:.0f}ms SLO."
                    if is_capacity_binding
                    else f"Headroom available: {capacity_slack:.0f} RPS capacity margin above current traffic."
                ),
                inequality=f"admitted_rps <= (replicas * cpu_milli / 1000) * {rps_per_core}",
                bound_value=max_service_capacity,
                actual_value=float(config.admitted_rps),
                slack=capacity_slack,
                is_binding=is_capacity_binding,
            )
        )

        # 3. Cache Requirement Invariant (Task 12-14 discovery)
        is_cache_forced = config.admitted_rps > self.cache_forced_threshold_rps
        constraints.append(
            ActiveConstraint(
                name="Cache Requirement Invariant",
                category="caching",
                description=(
                    f"Cache requirement enforced: Traffic ({config.admitted_rps} RPS) exceeds {self.cache_forced_threshold_rps} RPS; "
                    "operating uncached triggers cascading DB queue saturation and latency violation."
                    if is_cache_forced
                    else f"Traffic below {self.cache_forced_threshold_rps} RPS; caching is optional."
                ),
                inequality=f"cache_enabled == 1 (for RPS > {self.cache_forced_threshold_rps})",
                bound_value=float(self.cache_forced_threshold_rps),
                actual_value=float(config.admitted_rps),
                slack=max(0.0, float(self.cache_forced_threshold_rps - config.admitted_rps)),
                is_binding=is_cache_forced,
            )
        )

        # 4. Database Connection Pool Concurrency Floor
        total_cluster_conns = config.checkout_replicas * config.db_pool_size
        min_total_conns = math.ceil(
            config.admitted_rps / float(self.concurrency_per_pool_conn)
        )
        pool_slack = total_cluster_conns - min_total_conns
        is_pool_binding = pool_slack <= 2

        constraints.append(
            ActiveConstraint(
                name="DB Pool Concurrency Floor",
                category="database",
                description=(
                    f"DB connection floor active: Total cluster connections ({total_cluster_conns}) is at the minimum concurrency threshold "
                    f"for {config.admitted_rps} RPS to prevent connection queue buildup."
                    if is_pool_binding
                    else f"DB pool has {pool_slack} spare total connections beyond cluster concurrency minimum."
                ),
                inequality=f"replicas * db_pool_size >= ceil(admitted_rps / {self.concurrency_per_pool_conn})",
                bound_value=float(min_total_conns),
                actual_value=float(total_cluster_conns),
                slack=float(pool_slack),
                is_binding=is_pool_binding,
            )
        )

        # 5. High-Load DB Pool Constraint (Task 12-14 discovery)
        is_high_load_active = (
            config.admitted_rps > self.high_load_threshold_rps
            and config.db_pool_size == self.high_load_min_pool
        )
        if config.admitted_rps > self.high_load_threshold_rps:
            constraints.append(
                ActiveConstraint(
                    name="High-Load DB Pool Constraint",
                    category="database",
                    description=(
                        f"Tightened DB constraint binding: Validated rule 'db_pool_size <= 25 and target_rps > 400 => SLO_VIOLATION' "
                        f"actively bounds pool size to at least {self.high_load_min_pool}."
                    ),
                    inequality=f"db_pool_size >= {self.high_load_min_pool}",
                    bound_value=float(self.high_load_min_pool),
                    actual_value=float(config.db_pool_size),
                    slack=float(config.db_pool_size - self.high_load_min_pool),
                    is_binding=is_high_load_active,
                )
            )

        # 6. Global PostgreSQL Max Connections Ceiling
        total_cluster_conns = config.checkout_replicas * config.db_pool_size
        max_conn_slack = self.max_db_connections - total_cluster_conns
        is_max_conn_binding = max_conn_slack <= 10

        constraints.append(
            ActiveConstraint(
                name="PostgreSQL Global Connection Ceiling",
                category="database",
                description=(
                    f"Global DB connection limit binding: Total cluster connections ({total_cluster_conns}) is near max limit ({self.max_db_connections})."
                    if is_max_conn_binding
                    else f"Global connection margin: {max_conn_slack} spare connections available."
                ),
                inequality=f"replicas * db_pool_size <= {self.max_db_connections}",
                bound_value=float(self.max_db_connections),
                actual_value=float(total_cluster_conns),
                slack=float(max_conn_slack),
                is_binding=is_max_conn_binding,
            )
        )

        # 7. Hardware Resource Bounds
        is_min_rep_binding = config.checkout_replicas == self.min_replicas
        if is_min_rep_binding:
            constraints.append(
                ActiveConstraint(
                    name="Minimum Replica Baseline",
                    category="resource_bound",
                    description=f"Replica count is at minimum baseline ({self.min_replicas} pod).",
                    inequality=f"replicas >= {self.min_replicas}",
                    bound_value=float(self.min_replicas),
                    actual_value=float(config.checkout_replicas),
                    slack=0.0,
                    is_binding=True,
                )
            )

        is_min_cpu_binding = config.cpu_limit_milli == self.min_cpu_milli
        if is_min_cpu_binding:
            constraints.append(
                ActiveConstraint(
                    name="Minimum CPU Limit Baseline",
                    category="resource_bound",
                    description=f"CPU limit is at minimum container baseline ({self.min_cpu_milli}m).",
                    inequality=f"cpu_limit >= {self.min_cpu_milli}m",
                    bound_value=float(self.min_cpu_milli),
                    actual_value=float(config.cpu_limit_milli),
                    slack=0.0,
                    is_binding=True,
                )
            )

        return constraints

    def _generate_synthesis_explanation(
        self,
        workload_rps: int,
        config: CheapestConfig,
        cost: CostBreakdown,
        active_constraints: list[ActiveConstraint],
    ) -> str:
        """Construct a concise, human-readable paragraph synthesizing the active drivers."""
        binding_names = [c.name for c in active_constraints if c.is_binding]
        binding_summary = ", ".join(f"'{name}'" for name in binding_names)

        return (
            f"At a workload of {workload_rps} RPS, the optimizer selected {config.checkout_replicas} Checkout "
            f"replica(s) with {config.cpu_limit} CPU and db_pool_size={config.db_pool_size} "
            f"(cache={'enabled' if config.cache_enabled else 'disabled'}), admitting {config.admitted_rps} RPS "
            f"({config.admission_rate_pct}%). Total estimated cost is ${cost.total_hourly_cost:.4f}/hour. "
            f"Active binding constraints driving this configuration: [{binding_summary}]."
        )
