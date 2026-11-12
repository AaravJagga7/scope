"""Experiment configuration specifications and safety validation for SCOPE-X."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class WorkloadSpec:
    """Workload specification defining traffic characteristics."""

    rps: int
    profile: str = "normal"
    duration: int = 10
    warmup_duration: int = 2

    def validate(self) -> None:
        if self.rps <= 0:
            raise ValueError(f"Workload rps must be positive, got {self.rps}")
        valid_profiles = {"normal", "flash_sale", "ramp", "viral_product", "slow_payment_api"}
        if self.profile not in valid_profiles:
            raise ValueError(f"Invalid profile '{self.profile}'. Allowed: {sorted(valid_profiles)}")
        if self.duration <= 0:
            raise ValueError(f"Duration must be positive, got {self.duration}")
        if self.warmup_duration < 0:
            raise ValueError(f"Warmup duration cannot be negative, got {self.warmup_duration}")


@dataclass
class HPASpec:
    """Horizontal Pod Autoscaler configuration specification."""

    metric: str = "cpu"
    target_cpu_utilization_pct: int = 70
    min_replicas: int = 1
    max_replicas: int = 8
    scale_up_stabilization_seconds: int = 0
    scale_down_stabilization_seconds: int = 60
    scale_up_policy: str = "percent"
    scale_up_value: int = 100
    scale_down_policy: str = "percent"
    scale_down_value: int = 100

    def validate(self) -> None:
        if self.metric not in {"cpu", "memory"}:
            raise ValueError(f"HPA metric must be 'cpu' or 'memory', got '{self.metric}'")
        if not (1 <= self.target_cpu_utilization_pct <= 100):
            raise ValueError(
                f"target_cpu_utilization_pct must be between 1 and 100, got {self.target_cpu_utilization_pct}"
            )
        if self.min_replicas < 1:
            raise ValueError(f"min_replicas must be >= 1, got {self.min_replicas}")
        if self.max_replicas < self.min_replicas:
            raise ValueError(
                f"max_replicas ({self.max_replicas}) cannot be less than min_replicas ({self.min_replicas})"
            )
        if self.scale_up_stabilization_seconds < 0:
            raise ValueError(
                f"scale_up_stabilization_seconds must be >= 0, got {self.scale_up_stabilization_seconds}"
            )
        if self.scale_down_stabilization_seconds < 0:
            raise ValueError(
                f"scale_down_stabilization_seconds must be >= 0, got {self.scale_down_stabilization_seconds}"
            )


@dataclass
class VPASpec:
    """Vertical Pod Autoscaler configuration specification."""

    mode: str = "auto"  # "auto" (apply changes) or "recommendation" (recommendation only)
    target_metric: str = "memory"  # "memory" or "cpu"
    cpu_request: str = "250m"
    cpu_limit: str = "500m"
    memory_request: str = "256Mi"
    memory_limit: str = "512Mi"
    cpu_levels: list[str] = field(default_factory=lambda: ["250m", "500m", "1000m", "2000m"])
    memory_levels: list[str] = field(default_factory=lambda: ["256Mi", "512Mi", "1024Mi", "2048Mi"])
    in_place_resize: bool = True

    def validate(self) -> None:
        valid_modes = {"auto", "recommendation"}
        if self.mode not in valid_modes:
            raise ValueError(f"VPA mode must be one of {sorted(valid_modes)}, got '{self.mode}'")
        if self.target_metric not in {"cpu", "memory"}:
            raise ValueError(
                f"VPA target_metric must be 'cpu' or 'memory', got '{self.target_metric}'"
            )


@dataclass
class ExperimentConfig:
    """Complete specification of a SCOPE-X experimental configuration."""

    workload: WorkloadSpec
    checkout_replicas: int
    db_pool_size: int
    cpu_limit: str = "500m"
    cache_enabled: bool = True
    consumers: int = 1
    seed: int = 42
    split: str = "train"  # Choices: "train", "held_out", "test"
    scaling_mode: str = "static"  # Choices: "static", "hpa", "vertical"
    hpa: HPASpec | None = None
    vpa: VPASpec | None = None
    experiment_id: str = field(default_factory=lambda: f"exp_{uuid.uuid4().hex[:12]}")
    description: str = ""
    tags: dict[str, str] = field(default_factory=dict)

    def validate(self) -> None:
        """Validates all parameter values."""
        self.workload.validate()
        if self.checkout_replicas <= 0:
            raise ValueError(f"checkout_replicas must be positive, got {self.checkout_replicas}")
        if self.db_pool_size <= 0:
            raise ValueError(f"db_pool_size must be positive, got {self.db_pool_size}")
        if self.consumers <= 0:
            raise ValueError(f"consumers must be positive, got {self.consumers}")
        valid_splits = {"train", "held_out", "test"}
        if self.split not in valid_splits:
            raise ValueError(
                f"Invalid split tag '{self.split}'. Must be one of {sorted(valid_splits)}"
            )
        valid_modes = {"static", "hpa", "vertical"}
        if self.scaling_mode not in valid_modes:
            raise ValueError(
                f"Invalid scaling_mode '{self.scaling_mode}'. Must be one of {sorted(valid_modes)}"
            )
        if self.scaling_mode == "hpa" and self.hpa is None:
            self.hpa = HPASpec()
        if self.scaling_mode == "vertical" and self.vpa is None:
            self.vpa = VPASpec()
        if self.hpa is not None:
            self.hpa.validate()
        if self.vpa is not None:
            self.vpa.validate()

    def to_dict(self) -> dict[str, Any]:
        """Serializes config to dictionary."""
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExperimentConfig:
        """Constructs an ExperimentConfig from dictionary."""
        data_copy = dict(data)
        workload_raw = data_copy.get("workload", {})
        if isinstance(workload_raw, dict):
            data_copy["workload"] = WorkloadSpec(**workload_raw)

        hpa_raw = data_copy.get("hpa")
        if isinstance(hpa_raw, dict):
            data_copy["hpa"] = HPASpec(**hpa_raw)

        vpa_raw = data_copy.get("vpa")
        if isinstance(vpa_raw, dict):
            data_copy["vpa"] = VPASpec(**vpa_raw)

        return cls(**data_copy)

    def to_json(self) -> str:
        """Serializes config to JSON string."""
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_json(cls, json_str: str) -> ExperimentConfig:
        """Deserializes config from JSON string."""
        return cls.from_dict(json.loads(json_str))


@dataclass(frozen=True)
class SafetyLimits:
    """Cluster resource ceiling safety policies to prevent overloading infrastructure."""

    max_rps: int = 2500
    max_replicas: int = 32
    min_replicas: int = 1
    max_db_pool_size: int = 150
    min_db_pool_size: int = 2
    max_consumers: int = 64

    def check_safety(self, config: ExperimentConfig) -> None:
        """Verifies that an experiment configuration does not violate safety ceilings."""
        config.validate()

        if config.workload.rps > self.max_rps:
            raise ValueError(
                f"Safety Violation: target RPS {config.workload.rps} exceeds safety ceiling of {self.max_rps} RPS"
            )
        if config.checkout_replicas > self.max_replicas:
            raise ValueError(
                f"Safety Violation: checkout replicas {config.checkout_replicas} exceeds safety ceiling of {self.max_replicas}"
            )
        if config.checkout_replicas < self.min_replicas:
            raise ValueError(
                f"Safety Violation: checkout replicas {config.checkout_replicas} below minimum {self.min_replicas}"
            )
        if config.db_pool_size > self.max_db_pool_size:
            raise ValueError(
                f"Safety Violation: db_pool_size {config.db_pool_size} exceeds safety ceiling of {self.max_db_pool_size}"
            )
        if config.db_pool_size < self.min_db_pool_size:
            raise ValueError(
                f"Safety Violation: db_pool_size {config.db_pool_size} below minimum of {self.min_db_pool_size}"
            )
        if config.consumers > self.max_consumers:
            raise ValueError(
                f"Safety Violation: consumer count {config.consumers} exceeds ceiling of {self.max_consumers}"
            )

        # Invariant: Never run HPA and VPA on the same metric (CPU) at the same time
        if config.hpa is not None and config.vpa is not None:
            hpa_metric = (config.hpa.metric or "cpu").strip().lower()
            vpa_metric = (config.vpa.target_metric or "memory").strip().lower()
            if hpa_metric == vpa_metric:
                raise ValueError(
                    f"Safety Violation: Simultaneous HPA and VPA autoscaling on the same metric ('{hpa_metric}') "
                    "is strictly prohibited. Conflicting controllers induce destructive scaling oscillations."
                )

        if config.hpa is not None:
            if config.hpa.max_replicas > self.max_replicas:
                raise ValueError(
                    f"Safety Violation: HPA max_replicas {config.hpa.max_replicas} exceeds safety ceiling of {self.max_replicas}"
                )
            if config.hpa.min_replicas < self.min_replicas:
                raise ValueError(
                    f"Safety Violation: HPA min_replicas {config.hpa.min_replicas} below safety floor of {self.min_replicas}"
                )


DEFAULT_SAFETY_LIMITS = SafetyLimits()
