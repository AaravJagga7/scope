"""Scale Manager settings.

Replica bounds match the SCOPE-X search space (experiment_engine/
experiment_config.py: replicas 1-6), so the Scale Manager refuses any
change the OR engine could not have asked for.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)


@dataclass
class ServiceTarget:
    deployment: str
    label_selector: str
    min_replicas: int = 1
    max_replicas: int = 6


@dataclass
class ScaleSettings:
    namespace: str = "default"
    kubectl: str = os.environ.get("SCOPEX_KUBECTL", "kubectl")
    context: str = os.environ.get("SCOPEX_KUBE_CONTEXT", "")   # "" = current context
    ready_timeout_s: float = 180.0
    poll_interval_s: float = 1.0
    # infrastructure change log - separate from the simulated datasets
    log_path: str = os.path.join(PROJECT, "datasets", "infra", "scale_events.csv")
    services: Dict[str, ServiceTarget] = field(default_factory=lambda: {
        "checkout": ServiceTarget("checkout", "app=checkout"),
    })


DEFAULT_SETTINGS = ScaleSettings()
