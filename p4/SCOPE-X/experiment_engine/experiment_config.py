"""Experimental search space, SLOs and safety limits.

x = (lambda, r, a, K, B)
  lambda : peak incoming request rate (req/s)
  r      : Checkout replica count
  a      : shield admission rate = token refill rate (req/s)
  K      : waiting-room capacity
  B      : token-bucket capacity

All ranges are first-guess calibrations for the single-process mock
Checkout. Re-calibrate them against the team's Kubernetes testbed.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Dict, List, Sequence

import numpy as np


@dataclass(frozen=True)
class Variable:
    name: str
    symbol: str
    low: float
    high: float
    integer: bool = False
    levels: Sequence[float] = ()      # grid levels used by grid search

    def clip(self, v: float) -> float:
        v = float(np.clip(v, self.low, self.high))
        return float(round(v)) if self.integer else v


@dataclass
class SearchSpace:
    variables: List[Variable] = field(default_factory=lambda: [
        Variable("traffic_rate", "lambda", 100, 800, False, (100, 275, 450, 625, 800)),
        Variable("replicas", "r", 1, 6, True, (1, 2, 3, 4, 5, 6)),
        Variable("admission_rate", "a", 50, 600, False, (50, 187.5, 325, 462.5, 600)),
        Variable("queue_capacity", "K", 100, 1000, True, (100, 550, 1000)),
        Variable("bucket_capacity", "B", 10, 200, True, (10, 105, 200)),
    ])

    @property
    def names(self) -> List[str]:
        return [v.name for v in self.variables]

    @property
    def dim(self) -> int:
        return len(self.variables)

    def to_unit(self, X: np.ndarray) -> np.ndarray:
        lo = np.array([v.low for v in self.variables])
        hi = np.array([v.high for v in self.variables])
        return (np.asarray(X, float) - lo) / (hi - lo)

    def from_unit(self, U: np.ndarray) -> np.ndarray:
        lo = np.array([v.low for v in self.variables])
        hi = np.array([v.high for v in self.variables])
        X = lo + np.asarray(U, float) * (hi - lo)
        for j, v in enumerate(self.variables):
            if v.integer:
                X[..., j] = np.round(X[..., j])
        return X

    def sample(self, n: int, rng: np.random.Generator) -> np.ndarray:
        return self.from_unit(rng.random((n, self.dim)))

    def grid(self) -> np.ndarray:
        mesh = np.meshgrid(*[np.asarray(v.levels, float) for v in self.variables], indexing="ij")
        return np.stack([m.ravel() for m in mesh], axis=1)

    def as_dict(self, x: Sequence[float]) -> Dict[str, float]:
        return {v.name: v.clip(xi) for v, xi in zip(self.variables, x)}


@dataclass
class SLO:
    """Operational requirements (initial values; justify experimentally)."""
    L95_max_ms: float = 250.0     # p95 checkout latency
    E_max: float = 0.01           # checkout error (timeout) rate
    Qw95_max_ms: float = 3000.0   # p95 waiting-room time

    def satisfied(self, row: dict) -> bool:
        return (row["L95_ms"] <= self.L95_max_ms and row["error_rate"] <= self.E_max
                and row["Qw95_ms"] <= self.Qw95_max_ms)


@dataclass
class SafetyLimits:
    """Emergency-stop thresholds for the experiment controller.

    These are about protecting the testbed, not about the SLO. A run is
    stopped early when, for ``patience`` consecutive monitoring windows,
    windowed p95 latency exceeds ``L_stop_ms`` or CPU utilisation exceeds
    ``U_stop``. The waiting room is bounded by construction (Q <= K).
    """
    L_stop_ms: float = 1500.0
    U_stop: float = 0.995
    window_s: float = 0.5
    patience_latency: int = 2
    patience_cpu: int = 10


@dataclass
class WorkloadProfile:
    """Flash-sale shaped load: baseline -> burst at lambda -> baseline."""
    kind: str = "flash"           # "flash" | "constant"
    pre_s: float = 2.0
    burst_s: float = 12.0
    post_s: float = 2.0
    baseline_fraction: float = 0.25

    def to_dict(self) -> dict:
        return asdict(self)


DEFAULT_SPACE = SearchSpace()
DEFAULT_SLO = SLO()
DEFAULT_SAFETY = SafetyLimits()
DEFAULT_PROFILE = WorkloadProfile()
