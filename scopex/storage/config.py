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
        valid_profiles = {
            "normal",
            "flash_sale",
            "ramp",
            "viral_product",
            "slow_payment_api",
            "sinusoidal",
        }
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


def _parse_cpu_millicores(quantity: str) -> int:
    """Parses a Kubernetes CPU quantity (e.g. '250m', '2') into millicores."""
    q = str(quantity).strip().lower()
    try:
        if q.endswith("m"):
            return int(round(float(q[:-1])))
        return int(round(float(q) * 1000.0))
    except ValueError as exc:
        raise ValueError(f"Invalid CPU quantity '{quantity}'") from exc


def _format_cpu_millicores(millicores: float) -> str:
    """Formats millicores back into a canonical Kubernetes CPU quantity."""
    millis = max(1, int(round(millicores)))
    if millis % 1000 == 0:
        return str(millis // 1000)
    return f"{millis}m"


def _parse_memory_mib(quantity: str) -> float:
    """Parses a Kubernetes memory quantity (e.g. '512Mi', '1Gi') into MiB."""
    q = str(quantity).strip()
    lower = q.lower()
    for suffix, factor in (
        ("ki", 1.0 / 1024.0),
        ("mi", 1.0),
        ("gi", 1024.0),
        ("ti", 1024.0 * 1024.0),
    ):
        if lower.endswith(suffix):
            try:
                return float(q[: -len(suffix)]) * factor
            except ValueError as exc:
                raise ValueError(f"Invalid memory quantity '{quantity}'") from exc
    raise ValueError(f"Memory quantity '{quantity}' must use Ki, Mi, Gi, or Ti units")


def _format_memory_mib(mib: float) -> str:
    """Formats MiB back into a canonical Kubernetes memory quantity."""
    value = max(1.0, float(mib))
    if value >= 1024.0 and value % 1024.0 == 0:
        return f"{int(value // 1024)}Gi"
    return f"{int(round(value))}Mi"


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
    # Optional index into cpu_levels/memory_levels. When set, it overrides the
    # explicit *_request/*_limit values with the level's (request, limit) pair.
    level: int | None = None
    # Fraction of each level's limit used for the corresponding request.
    request_ratio: float = 0.5

    def validate(self) -> None:
        valid_modes = {"auto", "recommendation"}
        if self.mode not in valid_modes:
            raise ValueError(f"VPA mode must be one of {sorted(valid_modes)}, got '{self.mode}'")
        if self.target_metric not in {"cpu", "memory"}:
            raise ValueError(
                f"VPA target_metric must be 'cpu' or 'memory', got '{self.target_metric}'"
            )
        if not (0.0 < self.request_ratio <= 1.0):
            raise ValueError(f"VPA request_ratio must be in (0, 1], got {self.request_ratio}")
        if self.level is not None:
            if not isinstance(self.level, int) or isinstance(self.level, bool):
                raise ValueError(f"VPA level must be an integer index, got {self.level!r}")
            if self.level < 0:
                raise ValueError(f"VPA level must be >= 0, got {self.level}")
            max_level = min(len(self.cpu_levels), len(self.memory_levels)) - 1
            if self.level > max_level:
                raise ValueError(
                    f"VPA level {self.level} out of range; available levels are 0..{max_level} "
                    f"(cpu_levels={len(self.cpu_levels)}, memory_levels={len(self.memory_levels)})"
                )
        for level in self.cpu_levels:
            _parse_cpu_millicores(level)
        for level in self.memory_levels:
            _parse_memory_mib(level)

    def resolved_resources(self) -> dict[str, Any]:
        """Resolves the CPU/memory requests and limits for the selected vertical level.

        With no level selected the explicit cpu_request/cpu_limit/memory_request/
        memory_limit values are returned unchanged, preserving earlier behaviour.
        """
        if self.level is None:
            return {
                "level": None,
                "cpu_request": self.cpu_request,
                "cpu_limit": self.cpu_limit,
                "memory_request": self.memory_request,
                "memory_limit": self.memory_limit,
            }

        cpu_limit = self.cpu_levels[self.level]
        memory_limit = self.memory_levels[self.level]
        return {
            "level": self.level,
            "cpu_request": _format_cpu_millicores(
                _parse_cpu_millicores(cpu_limit) * self.request_ratio
            ),
            "cpu_limit": cpu_limit,
            "memory_request": _format_memory_mib(
                _parse_memory_mib(memory_limit) * self.request_ratio
            ),
            "memory_limit": memory_limit,
        }


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


AUTOSCALER_CONFLICT_MESSAGE = (
    "Safety Violation: Simultaneous HPA and VPA autoscaling on the same metric ('{metric}') "
    "is strictly prohibited. Conflicting controllers induce destructive scaling oscillations."
)


def assert_no_autoscaler_conflict(hpa_metric: str | None, vpa_metric: str | None) -> None:
    """Rejects running horizontal and vertical autoscalers against the same metric.

    Shared by :class:`SafetyLimits` and the cluster configuration controller so the
    invariant is enforced on every application path, not just the experiment runner.
    """
    if not hpa_metric or not vpa_metric:
        return
    hpa_norm = str(hpa_metric).strip().lower()
    vpa_norm = str(vpa_metric).strip().lower()
    if hpa_norm == vpa_norm:
        raise ValueError(AUTOSCALER_CONFLICT_MESSAGE.format(metric=hpa_norm))


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
            assert_no_autoscaler_conflict(config.hpa.metric, config.vpa.target_metric)

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
