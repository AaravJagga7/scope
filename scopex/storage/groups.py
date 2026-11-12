"""Declarative experiment group definitions and matrix resolution."""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from scopex.storage.config import ExperimentConfig, WorkloadSpec

DEFAULT_GROUPS_DIR = Path(__file__).resolve().parent.parent.parent / "experiments" / "groups"


@dataclass
class ExperimentGroup:
    """Represents a named declarative experiment group loaded from YAML."""

    group_id: str
    name: str
    description: str
    file_path: Path
    defaults: dict[str, Any] = field(default_factory=dict)
    workload_defaults: dict[str, Any] = field(default_factory=dict)
    matrix: dict[str, list[Any]] = field(default_factory=dict)
    configurations: list[ExperimentConfig] = field(default_factory=list)

    def summary(self) -> str:
        """Returns concise summary string of the group."""
        return (
            f"Group [{self.group_id}]: {self.name} "
            f"({len(self.configurations)} configurations) - {self.description}"
        )


def parse_group_yaml(file_path: Path | str) -> ExperimentGroup:
    """Parses a declarative experiment group YAML file and expands its parameter matrix."""
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"Group definition file not found: {path}")

    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    group_id = str(data.get("group", path.stem.split("_")[0])).upper()
    name = str(data.get("name", f"Group {group_id}"))
    description = str(data.get("description", ""))
    defaults = dict(data.get("defaults", {}))
    workload_defaults = dict(data.get("workload", {}))
    matrix = dict(data.get("matrix", {}))

    # Base defaults
    base_checkout_replicas = int(defaults.get("checkout_replicas", 2))
    base_db_pool_size = int(defaults.get("db_pool_size", 50))
    base_cpu_limit = str(defaults.get("cpu_limit", "500m"))
    base_cache_enabled = bool(defaults.get("cache_enabled", False))
    base_consumers = int(defaults.get("consumers", 2))
    base_seed = int(defaults.get("seed", 42))
    base_split = str(defaults.get("split", "train"))

    base_workload = {
        "rps": int(workload_defaults.get("rps", 100)),
        "profile": str(workload_defaults.get("profile", "normal")),
        "duration": int(workload_defaults.get("duration", 15)),
        "warmup_duration": int(workload_defaults.get("warmup_duration", 2)),
    }

    configurations: list[ExperimentConfig] = []
    workload_keys = {"rps", "profile", "duration", "warmup_duration"}

    if matrix:
        keys = list(matrix.keys())
        combos = list(itertools.product(*(matrix[k] for k in keys)))
        for combo in combos:
            param_map = dict(zip(keys, combo, strict=True))

            # Copy baseline dicts
            cur_workload = dict(base_workload)
            cur_replicas = base_checkout_replicas
            cur_db_pool = base_db_pool_size
            cur_cpu_limit = base_cpu_limit
            cur_cache = base_cache_enabled
            cur_consumers = base_consumers
            cur_seed = base_seed
            cur_split = base_split

            for k, val in param_map.items():
                if k in workload_keys:
                    cur_workload[k] = val
                elif k == "checkout_replicas":
                    cur_replicas = int(val)
                elif k == "db_pool_size":
                    cur_db_pool = int(val)
                elif k == "cpu_limit":
                    cur_cpu_limit = str(val)
                elif k == "cache_enabled":
                    cur_cache = bool(val)
                elif k == "consumers":
                    cur_consumers = int(val)
                elif k == "seed":
                    cur_seed = int(val)
                elif k == "split":
                    cur_split = str(val)

            w_spec = WorkloadSpec(
                rps=int(cur_workload["rps"]),
                profile=str(cur_workload["profile"]),
                duration=int(cur_workload["duration"]),
                warmup_duration=int(cur_workload["warmup_duration"]),
            )
            cfg = ExperimentConfig(
                workload=w_spec,
                checkout_replicas=cur_replicas,
                db_pool_size=cur_db_pool,
                cpu_limit=cur_cpu_limit,
                cache_enabled=cur_cache,
                consumers=cur_consumers,
                seed=cur_seed,
                split=cur_split,
                tags={"group": group_id},
                description=f"Group {group_id} trial ({param_map})",
            )
            cfg.validate()
            configurations.append(cfg)
    else:
        # Single configuration defined by defaults
        w_spec = WorkloadSpec(**base_workload)
        cfg = ExperimentConfig(
            workload=w_spec,
            checkout_replicas=base_checkout_replicas,
            db_pool_size=base_db_pool_size,
            cpu_limit=base_cpu_limit,
            cache_enabled=base_cache_enabled,
            consumers=base_consumers,
            seed=base_seed,
            split=base_split,
            tags={"group": group_id},
            description=f"Group {group_id} baseline",
        )
        cfg.validate()
        configurations.append(cfg)

    return ExperimentGroup(
        group_id=group_id,
        name=name,
        description=description,
        file_path=path,
        defaults=defaults,
        workload_defaults=workload_defaults,
        matrix=matrix,
        configurations=configurations,
    )


def list_groups(groups_dir: Path | str | None = None) -> list[ExperimentGroup]:
    """Lists and parses all experiment group YAML files in the groups directory."""
    dir_path = Path(groups_dir or DEFAULT_GROUPS_DIR)
    if not dir_path.is_dir():
        return []

    groups: list[ExperimentGroup] = []
    yaml_files = sorted(dir_path.glob("*.yaml")) + sorted(dir_path.glob("*.yml"))
    for yf in yaml_files:
        try:
            group = parse_group_yaml(yf)
            groups.append(group)
        except Exception:
            continue
    return sorted(groups, key=lambda g: g.group_id)


def load_group(
    group_id_or_path: str,
    groups_dir: Path | str | None = None,
) -> ExperimentGroup:
    """Loads an experiment group by letter (e.g. 'A', 'C', 'E', 'F', 'M') or file path."""
    target = group_id_or_path.strip()
    path_candidate = Path(target)
    if path_candidate.is_file():
        return parse_group_yaml(path_candidate)

    dir_path = Path(groups_dir or DEFAULT_GROUPS_DIR)
    clean_target = target.upper()

    # Search in directory
    for yf in dir_path.glob("*.y*ml"):
        if yf.stem.upper() == clean_target:
            return parse_group_yaml(yf)
        prefix = yf.stem.split("_")[0].upper()
        if prefix == clean_target:
            return parse_group_yaml(yf)

    available = [g.group_id for g in list_groups(dir_path)]
    raise ValueError(
        f"Experiment group '{target}' not found in {dir_path}. Available groups: {available}"
    )
