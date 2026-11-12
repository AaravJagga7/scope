"""Configurable cost model for microservice resource optimization in SCOPE-X."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass
class CostBreakdown:
    """Detailed itemized breakdown of hourly costs."""

    replica_base_cost: float
    cpu_cost: float
    db_pool_cost: float
    cache_cost: float
    penalty_cost: float
    total_hourly_cost: float

    def to_dict(self) -> dict[str, float]:
        return {
            "replica_base_cost": round(self.replica_base_cost, 4),
            "cpu_cost": round(self.cpu_cost, 4),
            "db_pool_cost": round(self.db_pool_cost, 4),
            "cache_cost": round(self.cache_cost, 4),
            "penalty_cost": round(self.penalty_cost, 4),
            "total_hourly_cost": round(self.total_hourly_cost, 4),
        }


@dataclass
class CostModel:
    """Configurable infrastructure and operational cost model.

    All monetary costs are expressed in USD per hour ($/hr).
    The model explicitly quantifies the trade-off between infrastructure
    over-provisioning and SLA violation / traffic loss penalties.

    Attributes:
        replica_base_cost_hourly: Fixed cost per pod/replica hour ($/pod-hr).
        cpu_core_cost_hourly: Cost per 1000m CPU (1 full core) per hour ($/core-hr).
        db_conn_cost_hourly: Overhead cost per allocated connection in DB pool ($/conn-hr).
        cache_cost_hourly: Fixed hourly cost if Redis/cache is enabled ($/hr).
        dropped_rps_penalty_hourly: Penalty cost per unserved/shed RPS per hour ($/RPS-hr).
        scale_factor: Integer scaling factor for CP-SAT linear objective mapping (default: 10000).
    """

    replica_base_cost_hourly: float = 0.0500  # $0.05 per replica container / hour
    cpu_core_cost_hourly: float = 0.0400      # $0.04 per CPU core / hour ($0.00004 / milliCPU)
    db_conn_cost_hourly: float = 0.0020       # $0.002 per DB pool connection / hour
    cache_cost_hourly: float = 0.0200         # $0.02 per hour for caching infrastructure
    dropped_rps_penalty_hourly: float = 0.5000  # $0.50 per dropped RPS-hour penalty
    scale_factor: int = 10_000                # Maps $0.0001 precision into CP-SAT integers

    def compute_cost(
        self,
        replicas: int,
        cpu_limit_milli: int,
        db_pool_size: int,
        cache_enabled: bool,
        workload_rps: int,
        admitted_rps: int,
    ) -> CostBreakdown:
        """Calculate the exact itemized cost breakdown for a configuration.

        Args:
            replicas: Number of service replica pods.
            cpu_limit_milli: CPU limit in milliCPUs (e.g. 500m = 500, 1000m = 1000).
            db_pool_size: Maximum connections in the database pool.
            cache_enabled: Whether cache layer is active.
            workload_rps: Total offered incoming traffic rate in RPS.
            admitted_rps: Actual admitted traffic rate in RPS.

        Returns:
            :class:`CostBreakdown` with itemized and total hourly costs.
        """
        # 1. Base replica pod overhead
        rep_cost = replicas * self.replica_base_cost_hourly

        # 2. CPU cost: (replicas * cpu_limit_milli / 1000) * core_cost
        total_cpu_cores = (replicas * cpu_limit_milli) / 1000.0
        cpu_cost = total_cpu_cores * self.cpu_core_cost_hourly

        # 3. Database connection pool overhead across all replica pods
        total_db_connections = replicas * db_pool_size
        db_cost = total_db_connections * self.db_conn_cost_hourly

        # 4. Cache infrastructure cost
        cache_cost = self.cache_cost_hourly if cache_enabled else 0.0

        # 5. Load shedding / SLA degradation penalty
        shed_rps = max(0, workload_rps - admitted_rps)
        penalty_cost = shed_rps * self.dropped_rps_penalty_hourly

        total_cost = rep_cost + cpu_cost + db_cost + cache_cost + penalty_cost

        return CostBreakdown(
            replica_base_cost=rep_cost,
            cpu_cost=cpu_cost,
            db_pool_cost=db_cost,
            cache_cost=cache_cost,
            penalty_cost=penalty_cost,
            total_hourly_cost=total_cost,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize cost model to a dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CostModel:
        """Create CostModel from dictionary, ignoring unknown keys."""
        valid_fields = cls.__dataclass_fields__.keys()
        filtered = {k: v for k, v in data.items() if k in valid_fields}
        return cls(**filtered)

    @classmethod
    def from_yaml(cls, path: str | Path) -> CostModel:
        """Load CostModel configuration from a YAML file."""
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return cls.from_dict(data or {})

    @classmethod
    def from_json(cls, path: str | Path) -> CostModel:
        """Load CostModel configuration from a JSON file."""
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return cls.from_dict(data or {})

    @classmethod
    def standard_cloud(cls) -> CostModel:
        """Standard production cloud pricing profile."""
        return cls(
            replica_base_cost_hourly=0.05,
            cpu_core_cost_hourly=0.04,
            db_conn_cost_hourly=0.002,
            cache_cost_hourly=0.02,
            dropped_rps_penalty_hourly=0.50,
        )

    @classmethod
    def strict_availability(cls) -> CostModel:
        """High-penalty profile that heavily penalizes any dropped request."""
        return cls(
            replica_base_cost_hourly=0.05,
            cpu_core_cost_hourly=0.04,
            db_conn_cost_hourly=0.002,
            cache_cost_hourly=0.02,
            dropped_rps_penalty_hourly=10.0,  # $10/RPS-hr dropped penalty
        )

    @classmethod
    def cost_saving(cls) -> CostModel:
        """Budget-conscious profile with low shedding penalty for non-critical workloads."""
        return cls(
            replica_base_cost_hourly=0.03,
            cpu_core_cost_hourly=0.03,
            db_conn_cost_hourly=0.001,
            cache_cost_hourly=0.015,
            dropped_rps_penalty_hourly=0.05,  # Cheap shedding
        )
