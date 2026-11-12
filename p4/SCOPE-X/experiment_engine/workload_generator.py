"""Workload generation: piecewise-constant Poisson arrivals.

``arrival_times`` produces the timestamps used by the discrete-event testbed
and by the in-process live runner. For HTTP load against the FastAPI shield
use ``experiment_engine/locustfile.py`` (Locust) instead.
"""
from __future__ import annotations

from typing import List, Tuple

import numpy as np

from .experiment_config import WorkloadProfile


def segments(peak_rate: float, profile: WorkloadProfile) -> List[Tuple[float, float, float]]:
    """(start_s, end_s, rate) segments for the profile."""
    if profile.kind == "constant":
        total = profile.pre_s + profile.burst_s + profile.post_s
        return [(0.0, total, peak_rate)]
    if profile.kind == "flash":
        base = peak_rate * profile.baseline_fraction
        t1 = profile.pre_s
        t2 = t1 + profile.burst_s
        t3 = t2 + profile.post_s
        return [(0.0, t1, base), (t1, t2, peak_rate), (t2, t3, base)]
    raise ValueError(f"unknown workload kind {profile.kind!r}")


def arrival_times(peak_rate: float, profile: WorkloadProfile,
                  rng: np.random.Generator) -> np.ndarray:
    out = []
    for start, end, rate in segments(peak_rate, profile):
        if rate <= 0 or end <= start:
            continue
        n = rng.poisson(rate * (end - start))
        out.append(np.sort(rng.uniform(start, end, n)))
    return np.concatenate(out) if out else np.empty(0)


def offered_rate(t: float, peak_rate: float, profile: WorkloadProfile) -> float:
    for start, end, rate in segments(peak_rate, profile):
        if start <= t < end:
            return rate
    return 0.0
