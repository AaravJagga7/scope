"""Unit and integration tests for SCOPE-X Locust workloads and run.py wrapper."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from workload.profiles import (
    PROFILES,
    compute_concurrency,
    get_profile_config,
)

BASE_DIR = Path(__file__).resolve().parent.parent


def test_all_five_profiles_exist() -> None:
    """Verifies that all 5 specified profiles are registered and configured."""
    expected_profiles = ["normal", "flash_sale", "ramp", "viral_product", "slow_payment_api"]
    for p in expected_profiles:
        assert p in PROFILES, f"Profile '{p}' missing from PROFILES"
        cfg = get_profile_config(p)
        assert cfg.name == p
        assert cfg.checkout_weight > 0
        assert cfg.min_wait >= 0
        assert cfg.max_wait >= cfg.min_wait


def test_invalid_profile_raises() -> None:
    """Verifies that requesting an unknown profile raises a clear ValueError."""
    with pytest.raises(ValueError, match="Unknown profile"):
        get_profile_config("nonexistent_profile")


def test_compute_concurrency_scaling() -> None:
    """Verifies concurrency scaling calculations for different target RPS values."""
    normal_cfg = get_profile_config("normal")
    flash_cfg = get_profile_config("flash_sale")

    users_50, spawn_50 = compute_concurrency(50, normal_cfg)
    users_500, spawn_500 = compute_concurrency(500, normal_cfg)

    assert users_500 > users_50
    assert spawn_500 > spawn_50

    # Flash sale should have aggressive immediate spawn rate
    _, flash_spawn = compute_concurrency(500, flash_cfg)
    assert flash_spawn >= 10.0


def test_workload_run_cli_execution(tmp_path: Path) -> None:
    """Verifies workload/run.py CLI execution generates a valid summary file."""
    out_file = tmp_path / "test_cli_summary.json"
    cmd = [
        sys.executable,
        str(BASE_DIR / "workload" / "run.py"),
        "--profile",
        "flash_sale",
        "--rps",
        "100",
        "--duration",
        "2",
        "--seed",
        "123",
        "--output",
        str(out_file),
        "--mock",
    ]

    res = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    assert res.returncode == 0, f"run.py failed:\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}"

    assert out_file.exists()
    disk_data = json.loads(out_file.read_text())

    # Validate schema
    assert disk_data["profile"] == "flash_sale"
    assert disk_data["target_rps"] == 100
    assert "achieved_rps" in disk_data
    assert isinstance(disk_data["achieved_rps"], (int, float))
    assert "error_count" in disk_data
    assert isinstance(disk_data["error_count"], int)
    assert "total_requests" in disk_data
    assert disk_data["total_requests"] > 0

    # Validate client-side latency percentiles
    lats = disk_data["client_side_latency_percentiles"]
    for p in ("p50", "p90", "p95", "p99", "min", "max", "avg"):
        assert p in lats, f"Percentile '{p}' missing from latency metrics"
        assert isinstance(lats[p], (int, float))


def test_all_profiles_via_cli(tmp_path: Path) -> None:
    """Smoke test ensuring each profile can execute via CLI without errors."""
    for p in ("normal", "flash_sale", "ramp", "viral_product", "slow_payment_api"):
        out_file = tmp_path / f"summary_{p}.json"
        cmd = [
            sys.executable,
            str(BASE_DIR / "workload" / "run.py"),
            "--profile",
            p,
            "--rps",
            "50",
            "--duration",
            "1",
            "--seed",
            "42",
            "--output",
            str(out_file),
            "--mock",
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        assert res.returncode == 0, f"Profile '{p}' failed:\n{res.stderr}"
        assert out_file.exists()
        data = json.loads(out_file.read_text())
        assert data["profile"] == p
        assert data["total_requests"] > 0
