"""Unit and integration tests for Task 14A autoscaling (HPA, VPA, and static modes)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from scopex.storage import (
    DEFAULT_SAFETY_LIMITS,
    ExperimentConfig,
    ExperimentRepository,
    ExperimentRunner,
    HPASpec,
    VPASpec,
    WorkloadSpec,
    assert_no_autoscaler_conflict,
)
from scripts import apply_config
from scripts.apply_config import (
    apply_configuration,
    apply_hpa,
    apply_vpa,
    generate_hpa_manifest,
    resolve_vpa_resources,
    supports_in_place_resize,
)


def test_hpa_vpa_spec_validation() -> None:
    """Verifies validation rules for HPASpec and VPASpec."""
    hpa = HPASpec(
        metric="cpu",
        target_cpu_utilization_pct=75,
        min_replicas=2,
        max_replicas=10,
        scale_up_stabilization_seconds=0,
        scale_down_stabilization_seconds=60,
    )
    hpa.validate()

    # Invalid metric
    with pytest.raises(ValueError, match="HPA metric must be"):
        HPASpec(metric="disk").validate()

    # Invalid target utilization
    with pytest.raises(ValueError, match="target_cpu_utilization_pct"):
        HPASpec(target_cpu_utilization_pct=150).validate()

    # Invalid replica bounds
    with pytest.raises(ValueError, match="max_replicas .* cannot be less than"):
        HPASpec(min_replicas=5, max_replicas=2).validate()

    # VPA Spec validation
    vpa = VPASpec(mode="recommendation", target_metric="cpu")
    vpa.validate()

    with pytest.raises(ValueError, match="VPA mode must be one of"):
        VPASpec(mode="invalid_mode").validate()


def test_experiment_config_scaling_modes() -> None:
    """Verifies ExperimentConfig serialization and handling of scaling_mode."""
    cfg = ExperimentConfig(
        workload=WorkloadSpec(rps=300, profile="ramp", duration=10),
        checkout_replicas=2,
        db_pool_size=30,
        scaling_mode="hpa",
        hpa=HPASpec(min_replicas=2, max_replicas=8, target_cpu_utilization_pct=70),
    )
    cfg.validate()

    d = cfg.to_dict()
    assert d["scaling_mode"] == "hpa"
    assert d["hpa"]["target_cpu_utilization_pct"] == 70

    # From dict roundtrip
    restored = ExperimentConfig.from_dict(d)
    assert restored.scaling_mode == "hpa"
    assert restored.hpa is not None
    assert restored.hpa.max_replicas == 8

    # From JSON roundtrip
    j = cfg.to_json()
    from_json_cfg = ExperimentConfig.from_json(j)
    assert from_json_cfg.scaling_mode == "hpa"
    assert from_json_cfg.hpa.min_replicas == 2


def test_safety_invariant_hpa_vpa_cpu_conflict() -> None:
    """Invariant: Never run HPA and VPA on the same metric (CPU) at the same time."""
    safety = DEFAULT_SAFETY_LIMITS

    conflicting_cfg = ExperimentConfig(
        workload=WorkloadSpec(rps=200, profile="normal", duration=10),
        checkout_replicas=2,
        db_pool_size=30,
        scaling_mode="hpa",
        hpa=HPASpec(metric="cpu"),
        vpa=VPASpec(target_metric="cpu"),
    )

    with pytest.raises(ValueError, match="Simultaneous HPA and VPA autoscaling on the same metric"):
        safety.check_safety(conflicting_cfg)

    # Disjoint metrics (HPA on CPU, VPA on memory) is allowed
    safe_cfg = ExperimentConfig(
        workload=WorkloadSpec(rps=200, profile="normal", duration=10),
        checkout_replicas=2,
        db_pool_size=30,
        scaling_mode="hpa",
        hpa=HPASpec(metric="cpu"),
        vpa=VPASpec(target_metric="memory"),
    )
    safety.check_safety(safe_cfg)  # Does not raise


def test_apply_config_hpa_and_vpa(tmp_path: Path) -> None:
    """Verifies that apply_config updates manifests, HPA, and VPA configurations."""
    # Test HPA application
    hpa_res = apply_hpa(
        {
            "min_replicas": 2,
            "max_replicas": 6,
            "target_cpu_utilization_pct": 75,
            "scale_up_stabilization_seconds": 0,
            "scale_down_stabilization_seconds": 30,
        },
        enabled=True,
        namespace="scopex",
        live_cluster=False,
    )
    assert hpa_res["enabled"] is True
    assert hpa_res["min_replicas"] == 2
    assert hpa_res["max_replicas"] == 6

    # Test HPA disable
    hpa_disable_res = apply_hpa({}, enabled=False, namespace="scopex", live_cluster=False)
    assert hpa_disable_res["enabled"] is False

    # Test VPA recommendation mode
    vpa_rec = apply_vpa(
        {
            "mode": "recommendation",
            "cpu_limit": "1000m",
            "memory_limit": "1024Mi",
        },
        service="checkout",
        live_cluster=False,
    )
    assert vpa_rec["mode"] == "recommendation"
    assert vpa_rec["applied"] is False
    assert vpa_rec["restart_window_seconds"] == 0.0

    # Test VPA in-place resize
    vpa_inplace = apply_vpa(
        {
            "mode": "auto",
            "cpu_limit": "1000m",
            "memory_limit": "1024Mi",
            "in_place_resize": True,
        },
        service="checkout",
        live_cluster=False,
    )
    assert vpa_inplace["in_place"] is True
    assert vpa_inplace["restart_window_seconds"] == 0.0

    # Test VPA rolling restart recording
    vpa_rolling = apply_vpa(
        {
            "mode": "auto",
            "cpu_limit": "2000m",
            "memory_limit": "2048Mi",
            "in_place_resize": False,
        },
        service="checkout",
        live_cluster=False,
    )
    assert vpa_rolling["in_place"] is False
    assert vpa_rolling["restart_window_seconds"] > 0.0


def test_vertical_levels_resolution() -> None:
    """Vertical scaling levels resolve into CPU/memory request+limit pairs."""
    vpa = VPASpec(
        level=2,
        cpu_levels=["250m", "500m", "1000m", "2000m"],
        memory_levels=["256Mi", "512Mi", "1024Mi", "2048Mi"],
    )
    vpa.validate()
    resolved = vpa.resolved_resources()
    assert resolved["level"] == 2
    assert resolved["cpu_limit"] == "1000m"
    assert resolved["cpu_request"] == "500m"
    assert resolved["memory_limit"] == "1024Mi"
    assert resolved["memory_request"] == "512Mi"

    # No level selected preserves the explicit values (earlier behaviour).
    explicit = VPASpec(cpu_request="300m", cpu_limit="1500m")
    explicit.validate()
    assert explicit.resolved_resources()["cpu_request"] == "300m"
    assert explicit.resolved_resources()["cpu_limit"] == "1500m"

    # Out-of-range levels are rejected.
    with pytest.raises(ValueError, match="VPA level .* out of range"):
        VPASpec(level=99).validate()

    # apply_config resolves levels the same way as the spec.
    resolved_cfg = resolve_vpa_resources({"level": 3})
    assert resolved_cfg["cpu_limit"] == "2000m"
    assert resolved_cfg["memory_limit"] == "2048Mi"


def test_hpa_manifest_parameterisation(tmp_path: Path) -> None:
    """HPA manifests carry the configured metric, limits, and behaviour windows."""
    manifest_path = tmp_path / "hpa-checkout.yaml"
    manifest = generate_hpa_manifest(
        min_replicas=2,
        max_replicas=9,
        target_cpu_pct=65,
        scale_up_stabilization=15,
        scale_down_stabilization=120,
        metric="memory",
        manifest_path=manifest_path,
    )
    spec = yaml.safe_load(manifest)["spec"]
    assert spec["minReplicas"] == 2
    assert spec["maxReplicas"] == 9
    resource = spec["metrics"][0]["resource"]
    assert resource["name"] == "memory"
    assert resource["target"]["averageUtilization"] == 65
    assert spec["behavior"]["scaleUp"]["stabilizationWindowSeconds"] == 15
    assert spec["behavior"]["scaleDown"]["stabilizationWindowSeconds"] == 120
    assert "memory" in manifest_path.read_text()

    with pytest.raises(ValueError, match="HPA metric must be"):
        generate_hpa_manifest(metric="disk", manifest_path=manifest_path)


def test_apply_config_rejects_conflicting_metrics(tmp_path: Path) -> None:
    """Requirement 4: apply_config refuses HPA and VPA on the same metric."""
    conflicting = tmp_path / "conflict.json"
    conflicting.write_text(
        json.dumps(
            {
                "scaling_mode": "hpa",
                "checkout": {"replicas": 2},
                "hpa": {"metric": "cpu", "min_replicas": 1, "max_replicas": 4},
                "vpa": {"mode": "recommendation", "target_metric": "cpu"},
            }
        )
    )
    with pytest.raises(ValueError, match="Simultaneous HPA and VPA autoscaling on the same metric"):
        apply_configuration(str(conflicting), dry_run=True)

    # The shared helper is what both the safety check and the controller use.
    with pytest.raises(ValueError, match="Simultaneous HPA and VPA"):
        assert_no_autoscaler_conflict("cpu", "CPU")
    assert_no_autoscaler_conflict("cpu", "memory")  # disjoint metrics are allowed


def test_in_place_resize_capability_detection(monkeypatch: pytest.MonkeyPatch) -> None:
    """In-place resize is used when supported and falls back to rolling restarts otherwise."""
    monkeypatch.setattr(apply_config.shutil, "which", lambda _: None)
    assert supports_in_place_resize() is False

    class _Result:
        returncode = 0
        stdout = json.dumps({"resources": [{"name": "pods"}, {"name": "pods/resize"}]})

    monkeypatch.setattr(apply_config.shutil, "which", lambda _: "/usr/local/bin/kubectl")
    monkeypatch.setattr(apply_config.subprocess, "run", lambda *a, **k: _Result())
    assert supports_in_place_resize() is True

    class _NoResize(_Result):
        stdout = json.dumps({"resources": [{"name": "pods"}]})

    monkeypatch.setattr(apply_config.subprocess, "run", lambda *a, **k: _NoResize())
    assert supports_in_place_resize() is False


def test_acceptance_ramp_workload_with_hpa(tmp_path: Path) -> None:
    """Acceptance Test: A ramp workload with HPA enabled shows replicas changing over time,

    and the stored run includes the replica-count time series and scaling events.
    """
    db_file = tmp_path / "acceptance_autoscaling.db"
    pq_file = tmp_path / "acceptance_autoscaling.parquet"
    repo = ExperimentRepository(db_path=db_file, parquet_path=pq_file)
    runner = ExperimentRunner(repository=repo, mock_mode=True)

    hpa_cfg = HPASpec(
        metric="cpu",
        target_cpu_utilization_pct=70,
        min_replicas=1,
        max_replicas=6,
        scale_up_stabilization_seconds=0,
        scale_down_stabilization_seconds=60,
    )

    config = ExperimentConfig(
        workload=WorkloadSpec(rps=350, profile="ramp", duration=10, warmup_duration=0),
        checkout_replicas=1,
        db_pool_size=40,
        scaling_mode="hpa",
        hpa=hpa_cfg,
        split="test",
        description="Task 14A HPA Ramp Workload Test",
    )

    run_record = runner.run(config)

    # 1. Verify run completed
    assert run_record.status == "COMPLETED"
    assert run_record.scaling_mode == "hpa"

    # 2. Verify replica count changed over time
    replica_counts = [pt["replicas"] for pt in run_record.replica_time_series]
    assert len(replica_counts) > 0
    assert min(replica_counts) < max(replica_counts), (
        f"Replicas should change over time during ramp workload, got: {replica_counts}"
    )

    # 2b. Verify the series also carries CPU/memory request and limit history
    for point in run_record.replica_time_series:
        assert {"cpu_request", "cpu_limit", "memory_request", "memory_limit"} <= set(point)
        assert point["cpu_limit"] == config.cpu_limit
        assert point["memory_limit"] == "512Mi"

    # 3. Verify scaling events were recorded with timestamps
    assert len(run_record.scaling_events) > 0
    first_event = run_record.scaling_events[0]
    assert first_event["event_type"] == "scale_up"
    assert "timestamp" in first_event
    assert first_event["from_replicas"] < first_event["to_replicas"]

    # 4. Verify pod lifecycle events recorded
    assert len(run_record.pod_lifecycle_events) >= max(replica_counts)
    for pod in run_record.pod_lifecycle_events:
        assert "pod_name" in pod
        assert "ready_at" in pod
        assert pod["status"] == "Ready"

    # 5. Verify persistence in repository and reload
    reloaded_runs = repo.get_runs_by_experiment(config.experiment_id)
    assert len(reloaded_runs) == 1
    reloaded = reloaded_runs[0]
    assert reloaded.scaling_mode == "hpa"
    assert len(reloaded.replica_time_series) == len(run_record.replica_time_series)
    assert len(reloaded.scaling_events) == len(run_record.scaling_events)
    assert (
        reloaded.replica_time_series[-1]["replicas"] > reloaded.replica_time_series[0]["replicas"]
    )


def test_vertical_autoscaling_recording(tmp_path: Path) -> None:
    """Verifies vertical autoscaling run recording for recommendation and rolling restart modes."""
    db_file = tmp_path / "vpa_test.db"
    pq_file = tmp_path / "vpa_test.parquet"
    repo = ExperimentRepository(db_path=db_file, parquet_path=pq_file)
    runner = ExperimentRunner(repository=repo, mock_mode=True)

    # Recommendation mode
    cfg_rec = ExperimentConfig(
        workload=WorkloadSpec(rps=100, profile="normal", duration=5, warmup_duration=0),
        checkout_replicas=2,
        db_pool_size=20,
        scaling_mode="vertical",
        vpa=VPASpec(mode="recommendation", cpu_limit="1000m", memory_limit="1024Mi"),
    )
    run_rec = runner.run(cfg_rec)
    assert run_rec.restart_window_seconds == 0.0
    assert len(run_rec.vpa_recommendations) == 1
    assert run_rec.vpa_recommendations[0]["recommended_cpu"] == "1000m"
    # Recommendation-only mode logs a non-applied scaling event with a timestamp.
    assert any(
        e["event_type"] == "vpa_recommendation" and e["applied"] is False and "timestamp" in e
        for e in run_rec.scaling_events
    )

    # Rolling restart mode
    cfg_rolling = ExperimentConfig(
        workload=WorkloadSpec(rps=100, profile="normal", duration=5, warmup_duration=0),
        checkout_replicas=2,
        db_pool_size=20,
        scaling_mode="vertical",
        vpa=VPASpec(mode="auto", in_place_resize=False, cpu_limit="1000m"),
    )
    run_rolling = runner.run(cfg_rolling)
    assert run_rolling.restart_window_seconds > 0.0
    assert len(run_rolling.pod_lifecycle_events) > 0
    assert any(
        e["event_type"] == "vertical_resize" and e["restart_window_seconds"] > 0
        for e in run_rolling.scaling_events
    )

    # Level-based vertical resize records the resolved limits in the time series.
    cfg_level = ExperimentConfig(
        workload=WorkloadSpec(rps=100, profile="normal", duration=3, warmup_duration=0),
        checkout_replicas=2,
        db_pool_size=20,
        scaling_mode="vertical",
        vpa=VPASpec(mode="auto", level=2, in_place_resize=True),
    )
    run_level = runner.run(cfg_level)
    point = run_level.replica_time_series[0]
    assert point["cpu_limit"] == "1000m"
    assert point["cpu_request"] == "500m"
    assert point["memory_limit"] == "1024Mi"
    assert point["memory_request"] == "512Mi"
