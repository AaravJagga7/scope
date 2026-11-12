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

    def to_dict(self) -> dict[str, Any]:
        """Serializes config to dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExperimentConfig:
        """Constructs an ExperimentConfig from dictionary."""
        workload_raw = data.get("workload", {})
        if isinstance(workload_raw, dict):
            workload = WorkloadSpec(**workload_raw)
        else:
            workload = workload_raw

        data_copy = dict(data)
        data_copy["workload"] = workload
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


DEFAULT_SAFETY_LIMITS = SafetyLimits()
