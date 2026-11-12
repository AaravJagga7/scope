"""Unit tests for Kubernetes manifests, Helm chart parameterisation, and apply_config.py."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from scripts.apply_config import (
    DEFAULT_STATE,
    apply_configuration,
    load_cluster_state,
    parse_target_config,
    save_cluster_state,
)

BASE_DIR = Path(__file__).resolve().parent.parent
HELM_DIR = BASE_DIR / "infra" / "helm" / "scopex"
K8S_DIR = BASE_DIR / "infra" / "k8s"


def test_helm_chart_metadata() -> None:
    """Verifies that Chart.yaml contains valid metadata."""
    chart_file = HELM_DIR / "Chart.yaml"
    assert chart_file.exists()
    data = yaml.safe_load(chart_file.read_text())
    assert data["apiVersion"] == "v2"
    assert data["name"] == "scopex"
    assert "version" in data


def test_helm_values_parameters() -> None:
    """Verifies that values.yaml parameterises all required services and metrics."""
    values_file = HELM_DIR / "values.yaml"
    assert values_file.exists()
    values = yaml.safe_load(values_file.read_text())

    # Check services parameterisation
    expected_services = ["product", "cart", "inventory", "payment", "order", "checkout"]
    for svc in expected_services:
        assert svc in values, f"Service '{svc}' missing in values.yaml"
        assert "replicas" in values[svc]
        assert "resources" in values[svc]
        assert "requests" in values[svc]["resources"]
        assert "limits" in values[svc]["resources"]

    # Parameterised requirements
    assert "cache_enabled" in values["product"]
    assert "db_pool_size" in values["inventory"]
    assert "db_pool_size" in values["order"]
    assert "consumer_count" in values["order"]

    # Infrastructure components
    assert "postgres" in values
    assert "redis" in values
    assert "rabbitmq" in values
    assert "prometheus" in values
    assert "grafana" in values


def test_k8s_manifests_exist_and_valid() -> None:
    """Verifies that standalone Kubernetes manifests exist for all components and are valid YAML."""
    manifest_files = [
        "namespace.yaml",
        "postgres.yaml",
        "redis.yaml",
        "rabbitmq.yaml",
        "product.yaml",
        "cart.yaml",
        "inventory.yaml",
        "payment.yaml",
        "order.yaml",
        "checkout.yaml",
        "prometheus.yaml",
        "grafana.yaml",
        "kustomization.yaml",
    ]
    for mf in manifest_files:
        p = K8S_DIR / mf
        assert p.exists(), f"K8s manifest '{mf}' missing in infra/k8s"
        docs = list(yaml.safe_load_all(p.read_text()))
        assert len(docs) > 0, f"Manifest '{mf}' is empty"


def test_parse_target_config_formats() -> None:
    """Verifies that parse_target_config parses nested, flat, and service-keyed JSON formats."""
    # Format 1: Nested under services
    cfg1 = {
        "services": {
            "checkout": {"replicas": 4, "cpu_limit": "500m"},
            "order": {"consumer_count": 3},
        }
    }
    parsed1 = parse_target_config(cfg1)
    assert parsed1["checkout"]["replicas"] == 4
    assert parsed1["checkout"]["cpu_limit"] == "500m"
    assert parsed1["order"]["consumer_count"] == 3

    # Format 2: Direct service keys
    cfg2 = {
        "checkout": {"replicas": 3},
        "inventory": {"db_pool_size": 25},
    }
    parsed2 = parse_target_config(cfg2)
    assert parsed2["checkout"]["replicas"] == 3
    assert parsed2["inventory"]["db_pool_size"] == 25

    # Format 3: Flat keys
    cfg3 = {
        "checkout_replicas": 5,
        "db_pool_size": 15,
        "cache_enabled": False,
        "consumer_count": 4,
    }
    parsed3 = parse_target_config(cfg3)
    assert parsed3["checkout"]["replicas"] == 5
    assert parsed3["order"]["db_pool_size"] == 15
    assert parsed3["inventory"]["db_pool_size"] == 15
    assert parsed3["product"]["cache_enabled"] is False
    assert parsed3["order"]["consumer_count"] == 4


def test_apply_config_checkout_scaling(tmp_path: Path) -> None:
    """Acceptance test: apply_config scales Checkout replicas from 2 to 4 and confirms readiness."""
    # Ensure fresh state with checkout replicas = 2
    state = json.loads(json.dumps(DEFAULT_STATE))
    state["services"]["checkout"]["replicas"] = 2
    save_cluster_state(state)

    # Create temporary config setting checkout replicas to 4
    cfg_file = tmp_path / "scale_checkout.json"
    cfg_file.write_text(json.dumps({"checkout": {"replicas": 4}}))

    # Apply configuration
    success = apply_configuration(str(cfg_file), timeout=10, seed=123)
    assert success is True

    # Verify updated cluster state
    updated_state = load_cluster_state()
    assert updated_state["services"]["checkout"]["replicas"] == 4


def test_apply_config_dry_run(tmp_path: Path) -> None:
    """Verifies that --dry-run does not modify persistent cluster state."""
    state = json.loads(json.dumps(DEFAULT_STATE))
    state["services"]["checkout"]["replicas"] = 2
    save_cluster_state(state)

    cfg_file = tmp_path / "dry_run_cfg.json"
    cfg_file.write_text(json.dumps({"checkout": {"replicas": 8}}))

    success = apply_configuration(str(cfg_file), dry_run=True)
    assert success is True

    # State should remain 2
    current_state = load_cluster_state()
    assert current_state["services"]["checkout"]["replicas"] == 2
