"""Runtime-configurable parameters for the Flash-sale Shield.

Every traffic-management knob lives here so the experiment engine (and the
OR engine through it) can change it between - or during - experiments.
Nothing in the shield hard-codes these values.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict, field, fields
from typing import Any, Dict


@dataclass
class ShieldConfig:
    # --- Rate limiter (token bucket) ---------------------------------------
    admission_rate: float = 200.0      # a : token refill rate, tokens/s
    bucket_capacity: int = 100         # B : max burst admitted at once

    # --- Virtual waiting room ----------------------------------------------
    queue_capacity: int = 500          # K : max requests waiting
    queue_policy: str = "fifo"         # only FIFO for the initial version
    max_wait_s: float = 10.0           # waiting longer than this -> 503

    # --- Admission control -------------------------------------------------
    max_inflight: int = 200            # cap on requests inside Checkout
    latency_guard_ms: float = 0.0      # 0 disables; else pause admission
                                       # when EWMA checkout latency exceeds it
    latency_ewma_alpha: float = 0.2

    # --- Backpressure ------------------------------------------------------
    queue_soft_limit: float = 0.8      # fill ratio -> "WARN" (Retry-After hint)
    downstream_limit: int = 150        # Checkout in-flight above this -> pause

    def update(self, **changes: Any) -> "ShieldConfig":
        valid = {f.name for f in fields(self)}
        unknown = set(changes) - valid
        if unknown:
            raise KeyError(f"unknown shield parameter(s): {sorted(unknown)}")
        for k, v in changes.items():
            setattr(self, k, type(getattr(self, k))(v))
        self.validate()
        return self

    def validate(self) -> None:
        if self.admission_rate <= 0:
            raise ValueError("admission_rate must be > 0")
        if self.bucket_capacity < 1:
            raise ValueError("bucket_capacity must be >= 1")
        if self.queue_capacity < 0:
            raise ValueError("queue_capacity must be >= 0")
        if self.queue_policy != "fifo":
            raise ValueError("only 'fifo' queue_policy is implemented")
        if not 0 < self.queue_soft_limit <= 1:
            raise ValueError("queue_soft_limit must be in (0, 1]")
        if self.max_inflight < 1 or self.downstream_limit < 1:
            raise ValueError("max_inflight and downstream_limit must be >= 1")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CheckoutConfig:
    """Mock Checkout + shared database model (testbed surrogate).

    A request holds a Checkout worker for its app phase and keeps holding it
    while it waits for, and uses, a connection from the *shared* DB pool.
    Adding replicas adds workers but not DB connections, so the bottleneck
    moves to the database at higher scale - the effect SCOPE-X studies.
    """
    replicas: int = 2                  # r
    workers_per_replica: int = 4
    app_time_ms: float = 20.0          # mean app (CPU) time per request
    db_pool_size: int = 8              # shared across all replicas
    db_write_ms: float = 12.0          # mean write time with no contention
    db_contention: float = 0.6         # write time *= 1 + c * active/pool
    service_cv: float = 0.5            # coefficient of variation (lognormal)
    timeout_ms: float = 2000.0         # Checkout latency above this = error

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
