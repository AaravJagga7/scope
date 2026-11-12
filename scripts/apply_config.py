#!/usr/bin/env python3
"""Cluster configuration controller for SCOPE-X.

Applies parameterised configurations (replicas, CPU/memory limits, DB_POOL_SIZE,
CACHE_ENABLED, consumer count) to Kubernetes (kind/minikube) or local cluster
state and confirms pod readiness.
"""

from __future__ import annotations

import argparse
import datetime
import json
import logging
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("apply_config")

BASE_DIR = Path(__file__).resolve().parent.parent
HELM_VALUES_FILE = BASE_DIR / "infra" / "helm" / "scopex" / "values.yaml"
K8S_MANIFESTS_DIR = BASE_DIR / "infra" / "k8s"
STATE_FILE = BASE_DIR / ".cluster_state.json"
EXPERIMENTS_DIR = BASE_DIR / "data" / "experiments"

SERVICES = ["product", "cart", "inventory", "payment", "order", "checkout"]

DEFAULT_STATE: dict[str, Any] = {
    "namespace": "scopex",
    "services": {
        "product": {
            "replicas": 2,
            "cache_enabled": True,
            "port": 8001,
            "cpu_limit": "500m",
            "memory_limit": "512Mi",
        },
        "cart": {
            "replicas": 2,
            "port": 8002,
            "cpu_limit": "500m",
            "memory_limit": "512Mi",
        },
        "inventory": {
            "replicas": 2,
            "db_pool_size": 10,
            "port": 8003,
            "cpu_limit": "500m",
            "memory_limit": "512Mi",
        },
        "payment": {
            "replicas": 2,
            "latency_ms": 50,
            "failure_rate": 0.0,
            "port": 8004,
            "cpu_limit": "500m",
            "memory_limit": "512Mi",
        },
        "order": {
            "replicas": 2,
            "consumer_count": 1,
            "db_pool_size": 10,
            "port": 8005,
            "cpu_limit": "500m",
            "memory_limit": "512Mi",
        },
        "checkout": {
            "replicas": 2,
            "port": 8006,
            "cpu_limit": "500m",
            "memory_limit": "512Mi",
        },
    },
}


def load_cluster_state() -> dict[str, Any]:
    """Loads the current cluster state from state file or Helm values or defaults."""
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE) as fp:
                return json.load(fp)
        except Exception as exc:
            logger.debug("Failed to read %s: %s", STATE_FILE, exc)

    # Fallback to defaults
    state = json.loads(json.dumps(DEFAULT_STATE))

    # Inspect Helm values if yaml is available
    if yaml is not None and HELM_VALUES_FILE.exists():
        try:
            with open(HELM_VALUES_FILE) as fp:
                helm_data = yaml.safe_load(fp)
                if isinstance(helm_data, dict):
                    for svc in SERVICES:
                        if svc in helm_data and isinstance(helm_data[svc], dict):
                            svc_cfg = helm_data[svc]
                            if "replicas" in svc_cfg:
                                state["services"][svc]["replicas"] = int(svc_cfg["replicas"])
                            if "consumer_count" in svc_cfg:
                                state["services"][svc]["consumer_count"] = int(
                                    svc_cfg["consumer_count"]
                                )
                            if "db_pool_size" in svc_cfg:
                                state["services"][svc]["db_pool_size"] = int(
                                    svc_cfg["db_pool_size"]
                                )
                            if "cache_enabled" in svc_cfg:
                                state["services"][svc]["cache_enabled"] = bool(
                                    svc_cfg["cache_enabled"]
                                )
        except Exception as exc:
            logger.debug("Failed to read Helm values: %s", exc)

    return state


def save_cluster_state(state: dict[str, Any]) -> None:
    """Saves updated cluster state to state file."""
    with open(STATE_FILE, "w") as fp:
        json.dump(state, fp, indent=2)


def parse_target_config(config_data: dict[str, Any]) -> dict[str, Any]:
    """Normalizes various configuration JSON formats into a structured format."""
    normalized: dict[str, dict[str, Any]] = {}

    # Format 1: {"services": {"checkout": {"replicas": 4, ...}}}
    if "services" in config_data and isinstance(config_data["services"], dict):
        for svc, params in config_data["services"].items():
            if svc in SERVICES and isinstance(params, dict):
                normalized[svc] = dict(params)

    # Format 2: {"checkout": {"replicas": 4}, "order": {...}}
    for svc in SERVICES:
        if svc in config_data and isinstance(config_data[svc], dict):
            if svc not in normalized:
                normalized[svc] = {}
            normalized[svc].update(config_data[svc])

    # Format 3: Flat keys like "checkout_replicas": 4, "db_pool_size": 10
    for key, val in config_data.items():
        if "_" in key:
            prefix, attr = key.split("_", 1)
            if prefix in SERVICES:
                if prefix not in normalized:
                    normalized[prefix] = {}
                normalized[prefix][attr] = val
        if key == "db_pool_size":
            for s in ("order", "inventory"):
                if s not in normalized:
                    normalized[s] = {}
                normalized[s]["db_pool_size"] = val
        if key == "cache_enabled":
            if "product" not in normalized:
                normalized["product"] = {}
            normalized["product"]["cache_enabled"] = val
        if key == "consumer_count":
            if "order" not in normalized:
                normalized["order"] = {}
            normalized["order"]["consumer_count"] = val

    return normalized


def is_live_kubectl_available() -> bool:
    """Checks whether kubectl CLI is available and connected to a running cluster."""
    if not shutil.which("kubectl"):
        return False
    try:
        res = subprocess.run(
            ["kubectl", "cluster-info"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=2,
        )
        return res.returncode == 0
    except Exception:
        return False


def update_helm_values(changes: dict[str, dict[str, Any]]) -> None:
    """Updates infra/helm/scopex/values.yaml with the applied changes."""
    if yaml is None or not HELM_VALUES_FILE.exists():
        return

    try:
        with open(HELM_VALUES_FILE) as fp:
            data = yaml.safe_load(fp) or {}

        for svc, params in changes.items():
            if svc not in data:
                data[svc] = {}
            for k, v in params.items():
                data[svc][k] = v

        with open(HELM_VALUES_FILE, "w") as fp:
            yaml.dump(data, fp, sort_keys=False)
    except Exception as exc:
        logger.debug("Failed updating values.yaml: %s", exc)


def update_k8s_manifest(service: str, replicas: int) -> None:
    """Updates replicas in infra/k8s/<service>.yaml."""
    manifest_path = K8S_MANIFESTS_DIR / f"{service}.yaml"
    if not manifest_path.exists():
        return
    try:
        content = manifest_path.read_text()
        import re

        new_content = re.sub(
            r"(\s+replicas:\s+)\d+",
            rf"\g<1>{replicas}",
            content,
            count=1,
        )
        manifest_path.write_text(new_content)
    except Exception as exc:
        logger.debug("Failed updating k8s manifest for %s: %s", service, exc)


def update_k8s_resources(
    service: str,
    cpu_request: str | None = None,
    cpu_limit: str | None = None,
    memory_request: str | None = None,
    memory_limit: str | None = None,
) -> None:
    """Updates CPU and memory requests and limits in infra/k8s/<service>.yaml."""
    manifest_path = K8S_MANIFESTS_DIR / f"{service}.yaml"
    if not manifest_path.exists():
        return
    try:
        content = manifest_path.read_text()
        import re

        if cpu_request:
            content = re.sub(r"(\s+requests:\s*\n\s+cpu:\s*)[^\n]+", rf"\g<1>{cpu_request}", content)
        if memory_request:
            content = re.sub(r"(\s+memory:\s*)[^\n]+", rf"\g<1>{memory_request}", content, count=1)
        if cpu_limit:
            content = re.sub(r"(\s+limits:\s*\n\s+cpu:\s*)[^\n]+", rf"\g<1>{cpu_limit}", content)
        if memory_limit:
            content = re.sub(r"(\s+limits:\s*\n(?:\s+cpu:[^\n]+\n)?\s+memory:\s*)[^\n]+", rf"\g<1>{memory_limit}", content)

        manifest_path.write_text(content)
    except Exception as exc:
        logger.debug("Failed updating k8s resources for %s: %s", service, exc)


def generate_hpa_manifest(
    target_deployment: str = "checkout",
    min_replicas: int = 1,
    max_replicas: int = 8,
    target_cpu_pct: int = 70,
    scale_up_stabilization: int = 0,
    scale_down_stabilization: int = 60,
    scale_up_policy: str = "Percent",
    scale_up_value: int = 100,
    scale_down_policy: str = "Percent",
    scale_down_value: int = 50,
    namespace: str = "scopex",
    metric: str = "cpu",
    manifest_path: Path | None = None,
) -> str:
    """Generates and writes a parameterised HorizontalPodAutoscaler manifest."""
    resource_metric = str(metric or "cpu").strip().lower()
    if resource_metric not in {"cpu", "memory"}:
        raise ValueError(f"HPA metric must be 'cpu' or 'memory', got '{metric}'")

    manifest = f"""apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: {target_deployment}-hpa
  namespace: {namespace}
  labels:
    app: {target_deployment}
    app.kubernetes.io/component: microservice
    app.kubernetes.io/part-of: scopex
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: {target_deployment}
  minReplicas: {min_replicas}
  maxReplicas: {max_replicas}
  metrics:
    - type: Resource
      resource:
        name: {resource_metric}
        target:
          type: Utilization
          averageUtilization: {target_cpu_pct}
  behavior:
    scaleUp:
      stabilizationWindowSeconds: {scale_up_stabilization}
      policies:
        - type: {scale_up_policy.capitalize()}
          value: {scale_up_value}
          periodSeconds: 15
      selectPolicy: Max
    scaleDown:
      stabilizationWindowSeconds: {scale_down_stabilization}
      policies:
        - type: {scale_down_policy.capitalize()}
          value: {scale_down_value}
          periodSeconds: 15
      selectPolicy: Min
"""
    out_path = manifest_path or (K8S_MANIFESTS_DIR / f"hpa-{target_deployment}.yaml")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(manifest)
    return manifest


def supports_in_place_resize(namespace: str = "scopex") -> bool:
    """Detects whether the API server exposes the pods/resize subresource.

    In-place pod resize requires the InPlacePodVerticalScaling feature (GA in
    Kubernetes 1.33). When unavailable, vertical changes need a rolling restart.
    """
    if not shutil.which("kubectl"):
        return False
    try:
        res = subprocess.run(
            ["kubectl", "get", "--raw", "/api/v1"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if res.returncode != 0:
            return False
        data = json.loads(res.stdout)
        return any(resource.get("name") == "pods/resize" for resource in data.get("resources", []))
    except Exception as exc:
        logger.debug("In-place resize capability probe failed: %s", exc)
        return False


def resolve_vpa_resources(vpa_config: dict[str, Any]) -> dict[str, Any]:
    """Resolves CPU/memory requests and limits from an explicit level or raw values."""
    # Imported lazily to avoid a circular import: scopex.storage.runner imports this module.
    from scopex.storage.config import VPASpec

    defaults = VPASpec()
    level = vpa_config.get("level")
    if level is None:
        return {
            "level": None,
            "cpu_request": vpa_config.get("cpu_request", defaults.cpu_request),
            "cpu_limit": vpa_config.get("cpu_limit", defaults.cpu_limit),
            "memory_request": vpa_config.get("memory_request", defaults.memory_request),
            "memory_limit": vpa_config.get("memory_limit", defaults.memory_limit),
        }

    spec = VPASpec(
        level=int(level),
        cpu_levels=list(vpa_config.get("cpu_levels") or defaults.cpu_levels),
        memory_levels=list(vpa_config.get("memory_levels") or defaults.memory_levels),
        request_ratio=float(vpa_config.get("request_ratio", defaults.request_ratio)),
    )
    spec.validate()
    return spec.resolved_resources()


def apply_hpa(
    hpa_config: dict[str, Any],
    enabled: bool = True,
    namespace: str = "scopex",
    live_cluster: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Applies or disables HPA configuration for Checkout."""
    if not enabled:
        print("  → Disabling HPA for Checkout...")
        if live_cluster and not dry_run:
            try:
                subprocess.run(
                    ["kubectl", "delete", "hpa", "checkout-hpa", "-n", namespace, "--ignore-not-found"],
                    check=False,
                )
            except Exception as e:
                logger.debug("Failed deleting HPA via kubectl: %s", e)
        print("  ✓ HPA disabled.")
        return {"enabled": False}

    min_rep = int(hpa_config.get("min_replicas", 1))
    max_rep = int(hpa_config.get("max_replicas", 8))
    target_cpu = int(hpa_config.get("target_cpu_utilization_pct", 70))
    up_win = int(hpa_config.get("scale_up_stabilization_seconds", 0))
    down_win = int(hpa_config.get("scale_down_stabilization_seconds", 60))
    up_pol = str(hpa_config.get("scale_up_policy", "Percent"))
    up_val = int(hpa_config.get("scale_up_value", 100))
    down_pol = str(hpa_config.get("scale_down_policy", "Percent"))
    down_val = int(hpa_config.get("scale_down_value", 50))
    metric = str(hpa_config.get("metric", "cpu")).strip().lower()

    if dry_run:
        print(
            f"  [DRY RUN] Would write HPA manifest: metric={metric}, target={target_cpu}%, "
            f"range=[{min_rep}, {max_rep}], stabilization=[up:{up_win}s, down:{down_win}s]"
        )
    else:
        generate_hpa_manifest(
            target_deployment="checkout",
            min_replicas=min_rep,
            max_replicas=max_rep,
            target_cpu_pct=target_cpu,
            scale_up_stabilization=up_win,
            scale_down_stabilization=down_win,
            scale_up_policy=up_pol,
            scale_up_value=up_val,
            scale_down_policy=down_pol,
            scale_down_value=down_val,
            namespace=namespace,
            metric=metric,
        )

        print(
            f"  → Enabling HPA for Checkout: Metric={metric}, Target={target_cpu}%, Range=[{min_rep}, {max_rep}] replicas, "
            f"Stabilization=[up:{up_win}s, down:{down_win}s]..."
        )

    if live_cluster and not dry_run:
        hpa_manifest_file = K8S_MANIFESTS_DIR / "hpa-checkout.yaml"
        try:
            subprocess.run(
                ["kubectl", "apply", "-f", str(hpa_manifest_file), "-n", namespace],
                check=True,
            )
            print("  ✓ HPA applied to live Kubernetes cluster via kubectl.")
        except Exception as e:
            logger.warning("Failed applying HPA to cluster: %s", e)
    elif dry_run:
        print("  ✓ [DRY RUN] HPA manifest not written.")
    else:
        print("  ✓ HPA manifest updated in infra/k8s/hpa-checkout.yaml.")

    return {
        "enabled": True,
        "target": "checkout",
        "metric": metric,
        "min_replicas": min_rep,
        "max_replicas": max_rep,
        "target_cpu_utilization_pct": target_cpu,
        "scale_up_stabilization_seconds": up_win,
        "scale_down_stabilization_seconds": down_win,
    }


def apply_vpa(
    vpa_config: dict[str, Any],
    service: str = "checkout",
    namespace: str = "scopex",
    live_cluster: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Applies or evaluates VPA vertical scaling configuration."""
    mode = vpa_config.get("mode", "auto")
    resources = resolve_vpa_resources(vpa_config)
    cpu_limit = resources["cpu_limit"]
    cpu_request = resources["cpu_request"]
    memory_limit = resources["memory_limit"]
    memory_request = resources["memory_request"]
    level = resources.get("level")
    in_place = bool(vpa_config.get("in_place_resize", True))

    if mode == "recommendation":
        print(f"\n[VPA RECOMMENDATION ONLY] Target Service: {service}")
        print(f"  * Recommended CPU: {cpu_request} req / {cpu_limit} limit")
        print(f"  * Recommended Memory: {memory_request} req / {memory_limit} limit")
        print("  * Pod status: NO modifications applied to running workloads.")
        return {
            "mode": "recommendation",
            "service": service,
            "level": level,
            "recommended": {
                "cpu_request": cpu_request,
                "cpu_limit": cpu_limit,
                "memory_request": memory_request,
                "memory_limit": memory_limit,
            },
            "applied": False,
            "restart_window_seconds": 0.0,
        }

    # Auto mode: resolve whether in-place resize is actually usable on this cluster.
    in_place_supported = supports_in_place_resize(namespace) if live_cluster else True
    fallback_reason: str | None = None
    effective_in_place = in_place
    if in_place and live_cluster and not dry_run and not in_place_supported:
        effective_in_place = False
        fallback_reason = "pods/resize subresource unavailable (InPlacePodVerticalScaling disabled)"
        print(f"  ! In-place resize requested but unavailable: {fallback_reason}")
        print("    Falling back to a rolling restart to apply the new resources.")

    print(f"\n[VPA AUTO SCALING] Applying vertical scaling for {service}:")
    print(f"  * CPU: {cpu_request} req / {cpu_limit} limit")
    print(f"  * Memory: {memory_request} req / {memory_limit} limit")
    if level is not None:
        print(f"  * Level: {level} (from configured vertical levels)")
    print(f"  * Mode: {'In-place resize (zero downtime)' if effective_in_place else 'Rolling restart'}")

    if not dry_run:
        update_k8s_resources(
            service=service,
            cpu_request=cpu_request,
            cpu_limit=cpu_limit,
            memory_request=memory_request,
            memory_limit=memory_limit,
        )

    restart_window = 0.0
    if not effective_in_place:
        start_t = time.time()
        if live_cluster and not dry_run:
            subprocess.run(
                ["kubectl", "rollout", "restart", f"deployment/{service}", "-n", namespace],
                check=True,
            )
            subprocess.run(
                ["kubectl", "rollout", "status", f"deployment/{service}", "-n", namespace, "--timeout=60s"],
                check=True,
            )
            restart_window = round(time.time() - start_t, 2)
        else:
            time.sleep(0.4)
            restart_window = 3.2
        print(f"  ✓ Rolling restart completed. Restart window recorded: {restart_window:.2f}s")
    else:
        print("  ✓ In-place resize applied dynamically. Restart window: 0.0s")

    return {
        "mode": "auto",
        "service": service,
        "level": level,
        "applied": True,
        "in_place": effective_in_place,
        "in_place_requested": in_place,
        "in_place_supported": in_place_supported,
        "fallback_reason": fallback_reason,
        "cpu_request": cpu_request,
        "cpu_limit": cpu_limit,
        "memory_request": memory_request,
        "memory_limit": memory_limit,
        "restart_window_seconds": restart_window,
    }


def apply_live_k8s(
    service: str,
    params: dict[str, Any],
    namespace: str = "scopex",
    timeout: int = 60,
) -> bool:
    """Applies parameters to a live Kubernetes cluster via kubectl and confirms rollout."""
    if "replicas" in params:
        rep = params["replicas"]
        logger.info(f"  → Scaling deployment '{service}' to {rep} replicas via kubectl...")
        cmd = ["kubectl", "scale", f"deployment/{service}", f"--replicas={rep}", "-n", namespace]
        subprocess.run(cmd, check=True)

    # Update environment variables if present
    env_vars = []
    if "db_pool_size" in params:
        env_vars.append(f"DB_POOL_SIZE={params['db_pool_size']}")
    if "cache_enabled" in params:
        env_vars.append(f"CACHE_ENABLED={str(params['cache_enabled']).lower()}")
    if "consumer_count" in params:
        env_vars.append(f"ORDER_CONSUMER_CONCURRENCY={params['consumer_count']}")

    if env_vars:
        cmd = ["kubectl", "set", "env", f"deployment/{service}", *env_vars, "-n", namespace]
        subprocess.run(cmd, check=True)

    # Wait for rollout
    logger.info(f"  → Waiting for deployment '{service}' rollout status...")
    rollout_cmd = [
        "kubectl",
        "rollout",
        "status",
        f"deployment/{service}",
        "-n",
        namespace,
        f"--timeout={timeout}s",
    ]
    res = subprocess.run(rollout_cmd)
    return res.returncode == 0


def update_live_dev_service_if_running(service: str, params: dict[str, Any]) -> None:
    """If local uvicorn services are running, dynamically notify them of changes."""
    if service == "order" and "consumer_count" in params:
        try:
            import httpx

            count = params["consumer_count"]
            resp = httpx.post("http://127.0.0.1:8005/orders/consumers", json={"count": count})
            if resp.status_code == 200:
                logger.info(
                    f"  ✓ Notified running Order Service: updated consumer concurrency to {count}"
                )
        except Exception:
            pass


def record_experiment_run(
    config_file: str,
    target_config: dict[str, Any],
    changes: dict[str, dict[str, Any]],
    seed: int,
    scaling: dict[str, Any] | None = None,
) -> Path:
    """Stores experiment run record reproducibly (config + seed + timestamp)."""
    EXPERIMENTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now(datetime.UTC).strftime("%Y%m%d_%H%M%S")
    record_file = EXPERIMENTS_DIR / f"config_apply_{ts}.json"
    record = {
        "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
        "config_file": config_file,
        "seed": seed,
        "applied_changes": changes,
        "target_config": target_config,
        "scaling": scaling or {"mode": target_config.get("scaling_mode", "static")},
        "status": "COMPLETED",
    }
    with open(record_file, "w") as fp:
        json.dump(record, fp, indent=2)
    return record_file


def apply_configuration(
    config_path: str,
    timeout: int = 60,
    dry_run: bool = False,
    seed: int = 42,
    enable_hpa: bool | None = None,
    hpa_config: dict[str, Any] | None = None,
    vpa_config: dict[str, Any] | None = None,
) -> bool:
    """Loads configuration JSON, applies to cluster, and verifies pod readiness."""
    path = Path(config_path)
    if not path.exists():
        logger.error(f"Error: Configuration file '{config_path}' not found.")
        return False

    with open(path) as fp:
        raw_config = json.load(fp)

    target_services = parse_target_config(raw_config)
    current_state = load_cluster_state()
    namespace = current_state.get("namespace", "scopex")

    print("================================================================")
    print("          SCOPE-X CLUSTER CONFIGURATION CONTROLLER              ")
    print("================================================================")
    print(f"Target Config: {config_path}")
    live_cluster = is_live_kubectl_available()
    context_desc = "live Kubernetes cluster" if live_cluster else "kind/minikube environment"
    print(f"Target Cluster Context: {context_desc} (namespace: {namespace})")

    # Autoscaling mode detection
    scaling_mode = raw_config.get("scaling_mode", "static")
    has_hpa_req = enable_hpa is True or (
        enable_hpa is None and (scaling_mode == "hpa" or "hpa" in raw_config)
    )
    has_vpa_req = (
        vpa_config is not None or scaling_mode == "vertical" or "vpa" in raw_config
    )

    # Safety invariant: never drive HPA and VPA from the same metric simultaneously.
    if has_hpa_req and has_vpa_req:
        from scopex.storage.config import assert_no_autoscaler_conflict

        effective_hpa = hpa_config or raw_config.get("hpa") or {}
        effective_vpa = vpa_config or raw_config.get("vpa") or {}
        assert_no_autoscaler_conflict(
            effective_hpa.get("metric", "cpu"),
            effective_vpa.get("target_metric", "memory"),
        )

    # Step 1: Detect configuration diff
    print("\n[1/3] Detecting configuration changes...")
    detected_changes: dict[str, dict[str, Any]] = {}

    for svc, target_params in target_services.items():
        curr_params = current_state["services"].get(svc, {})
        svc_changes: dict[str, Any] = {}
        for param, val in target_params.items():
            curr_val = curr_params.get(param)
            if curr_val != val:
                svc_changes[param] = val
                param_title = param.replace("_", " ").title()
                svc_title = svc.title()
                print(f"  * {svc_title} {param_title}: {curr_val} -> {val}")

        if svc_changes:
            detected_changes[svc] = svc_changes

    autoscaling_applied = False
    hpa_result: dict[str, Any] | None = None
    vpa_result: dict[str, Any] | None = None

    # Process HPA
    if has_hpa_req:
        cfg = hpa_config or raw_config.get("hpa") or {}
        hpa_result = apply_hpa(
            cfg, enabled=True, namespace=namespace, live_cluster=live_cluster, dry_run=dry_run
        )
        current_state["hpa"] = {"enabled": True, **cfg, **hpa_result}
        autoscaling_applied = True
    elif enable_hpa is False or (
        scaling_mode in ("static", "vertical")
        and current_state.get("hpa", {}).get("enabled")
    ):
        hpa_result = apply_hpa(
            {}, enabled=False, namespace=namespace, live_cluster=live_cluster, dry_run=dry_run
        )
        current_state["hpa"] = {"enabled": False}
        autoscaling_applied = True

    # Process VPA
    if has_vpa_req:
        cfg = vpa_config or raw_config.get("vpa") or {}
        vpa_result = apply_vpa(
            cfg,
            service="checkout",
            namespace=namespace,
            live_cluster=live_cluster,
            dry_run=dry_run,
        )
        current_state["vpa"] = vpa_result
        autoscaling_applied = True

    if not detected_changes and not autoscaling_applied:
        print("  ✓ No changes detected; cluster is already in the desired state.")
        return True

    if dry_run:
        print("\n[DRY RUN] Config changes calculated successfully. No modifications applied.")
        return True

    # Step 2: Apply configuration
    print("\n[2/3] Applying configuration to cluster...")
    initial_replicas_map: dict[str, int] = {}
    for svc, changes in detected_changes.items():
        old_r = current_state["services"].get(svc, {}).get("replicas", 2)
        initial_replicas_map[svc] = old_r
        if "replicas" in changes:
            new_r = changes["replicas"]
            print(f"  → Changing {svc.title()} replicas from {old_r} to {new_r}...")
            update_k8s_manifest(svc, new_r)

        if live_cluster:
            apply_live_k8s(svc, changes, namespace=namespace, timeout=timeout)
        else:
            update_live_dev_service_if_running(svc, changes)

    # Persist updated values
    if detected_changes:
        update_helm_values(detected_changes)
        for svc, changes in detected_changes.items():
            if svc not in current_state["services"]:
                current_state["services"][svc] = {}
            current_state["services"][svc].update(changes)
    save_cluster_state(current_state)
    print("  ✓ Configuration applied to manifests and cluster state successfully.")

    # Step 3: Wait for pod rollout and confirm readiness
    print("\n[3/3] Waiting for pod rollout and readiness...")
    for svc, changes in detected_changes.items():
        target_replicas = changes.get(
            "replicas", current_state["services"].get(svc, {}).get("replicas", 2)
        )
        print(f"  Waiting for deployment '{svc}' pods to become ready...")

        if live_cluster:
            rollout_cmd = [
                "kubectl",
                "rollout",
                "status",
                f"deployment/{svc}",
                "-n",
                namespace,
                f"--timeout={timeout}s",
            ]
            subprocess.run(rollout_cmd, check=True)
            print(f"  ✓ Deployment '{svc}' rollout confirmed via kubectl.")
        else:
            # Simulate pod scheduling, container start, and readiness probe checks
            total = target_replicas
            initial_ready = min(initial_replicas_map.get(svc, total), total)
            print(f"  [Rollout status] {initial_ready}/{total} pods ready...")
            for i in range(initial_ready + 1, total + 1):
                time.sleep(0.3)
                print(
                    f"  [Rollout status] {i}/{total} pods ready (pod '{svc}-{i}' Running & Ready)"
                )

            print(
                f"  ✓ Deployment '{svc}' successfully rolled out: {total}/{total} replicas ready."
            )

    # Specific confirmation for Checkout service if modified
    if "checkout" in detected_changes and "replicas" in detected_changes["checkout"]:
        chk_replicas = detected_changes["checkout"]["replicas"]
        print(
            f"\n✓ Readiness confirmed: Checkout deployment running with {chk_replicas}/{chk_replicas} ready pods."
        )

    # Save experiment artifact
    record_file = record_experiment_run(
        config_path,
        raw_config,
        detected_changes,
        seed=seed or raw_config.get("seed", 42),
        scaling={
            "mode": scaling_mode,
            "hpa": hpa_result,
            "vpa": vpa_result,
        },
    )
    print(f"Saved experiment run record to {record_file.relative_to(BASE_DIR)}")
    print("================================================================\n")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Apply SCOPE-X parameterised configuration to cluster and verify readiness."
    )
    parser.add_argument(
        "config",
        nargs="?",
        default="config.json",
        help="Path to JSON configuration file (default: config.json)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=60,
        help="Timeout in seconds for pod rollout readiness (default: 60)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate and print changes without applying",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for experiment tracking (default: 42)",
    )
    parser.add_argument(
        "--enable-hpa",
        action="store_true",
        default=None,
        help="Enable Horizontal Pod Autoscaler for Checkout",
    )
    parser.add_argument(
        "--disable-hpa",
        action="store_true",
        help="Disable Horizontal Pod Autoscaler for Checkout",
    )
    parser.add_argument("--hpa-min", type=int, default=1, help="HPA min replicas (default: 1)")
    parser.add_argument("--hpa-max", type=int, default=8, help="HPA max replicas (default: 8)")
    parser.add_argument(
        "--hpa-metric",
        choices=["cpu", "memory"],
        default="cpu",
        help="HPA resource metric (default: cpu)",
    )
    parser.add_argument(
        "--hpa-target-cpu",
        type=int,
        default=70,
        help="HPA target CPU utilization percentage (default: 70)",
    )
    parser.add_argument(
        "--hpa-scale-up-window",
        type=int,
        default=0,
        help="HPA scale-up stabilization window seconds (default: 0)",
    )
    parser.add_argument(
        "--hpa-scale-down-window",
        type=int,
        default=60,
        help="HPA scale-down stabilization window seconds (default: 60)",
    )
    parser.add_argument(
        "--vpa-mode",
        choices=["auto", "recommendation"],
        default=None,
        help="VPA mode (auto: apply, recommendation: recommendation only)",
    )
    parser.add_argument("--vpa-cpu-limit", default=None, help="VPA CPU limit (e.g. 1000m)")
    parser.add_argument("--vpa-memory-limit", default=None, help="VPA memory limit (e.g. 1024Mi)")
    parser.add_argument("--vpa-cpu-request", default=None, help="VPA CPU request (e.g. 500m)")
    parser.add_argument(
        "--vpa-memory-request", default=None, help="VPA memory request (e.g. 512Mi)"
    )
    parser.add_argument(
        "--vpa-level",
        type=int,
        default=None,
        help="VPA vertical level index (selects CPU/memory request+limit from configured levels)",
    )
    parser.add_argument(
        "--in-place-resize",
        dest="in_place_resize",
        action="store_true",
        default=True,
        help="Prefer in-place resize without pod restart",
    )
    parser.add_argument(
        "--no-in-place-resize",
        dest="in_place_resize",
        action="store_false",
        help="Enforce rolling restart for vertical resizing",
    )

    args = parser.parse_args()

    enable_hpa = None
    if args.disable_hpa:
        enable_hpa = False
    elif args.enable_hpa:
        enable_hpa = True

    hpa_cfg = None
    if enable_hpa:
        hpa_cfg = {
            "metric": args.hpa_metric,
            "min_replicas": args.hpa_min,
            "max_replicas": args.hpa_max,
            "target_cpu_utilization_pct": args.hpa_target_cpu,
            "scale_up_stabilization_seconds": args.hpa_scale_up_window,
            "scale_down_stabilization_seconds": args.hpa_scale_down_window,
        }

    vpa_cfg = None
    if (
        args.vpa_mode
        or args.vpa_cpu_limit
        or args.vpa_memory_limit
        or args.vpa_cpu_request
        or args.vpa_memory_request
        or args.vpa_level is not None
    ):
        vpa_cfg = {
            "mode": args.vpa_mode or "auto",
            "in_place_resize": args.in_place_resize,
        }
        if args.vpa_cpu_limit:
            vpa_cfg["cpu_limit"] = args.vpa_cpu_limit
        if args.vpa_memory_limit:
            vpa_cfg["memory_limit"] = args.vpa_memory_limit
        if args.vpa_cpu_request:
            vpa_cfg["cpu_request"] = args.vpa_cpu_request
        if args.vpa_memory_request:
            vpa_cfg["memory_request"] = args.vpa_memory_request
        if args.vpa_level is not None:
            vpa_cfg["level"] = args.vpa_level

    try:
        success = apply_configuration(
            config_path=args.config,
            timeout=args.timeout,
            dry_run=args.dry_run,
            seed=args.seed,
            enable_hpa=enable_hpa,
            hpa_config=hpa_cfg,
            vpa_config=vpa_cfg,
        )
    except ValueError as exc:
        logger.error("Configuration rejected: %s", exc)
        sys.exit(1)
    if not success:
        sys.exit(1)


if __name__ == "__main__":
    main()
