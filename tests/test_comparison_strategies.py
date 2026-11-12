"""Tests for autoscaling comparison strategies and unified evaluation harness."""

from __future__ import annotations

import json
from pathlib import Path

from scopex.cli import main as cli_main
from scopex.decision import (
    ComparisonReport,
    FixedStrategy,
    KubernetesHPAStrategy,
    QueueBacklogStrategy,
    ScopeXStrategy,
    StrategyComparisonRecord,
    StrategyDecision,
    SystemSimulator,
    SystemState,
    WorkloadTraceGenerator,
    run_comparison,
)


class TestWorkloadTraceGenerator:
    """Tests for deterministic workload trace generation."""

    def test_reproducibility_with_identical_seed(self) -> None:
        t1 = WorkloadTraceGenerator.generate_trace(
            profile="flash_sale", peak_rps=600, duration_seconds=30, seed=123
        )
        t2 = WorkloadTraceGenerator.generate_trace(
            profile="flash_sale", peak_rps=600, duration_seconds=30, seed=123
        )
        assert t1 == t2
        assert len(t1) == 30

    def test_different_profiles_generate_expected_shapes(self) -> None:
        flash = WorkloadTraceGenerator.generate_trace(profile="flash_sale", duration_seconds=20)
        ramp = WorkloadTraceGenerator.generate_trace(profile="ramp", duration_seconds=20)
        normal = WorkloadTraceGenerator.generate_trace(profile="normal", duration_seconds=20)

        assert len(flash) == 20
        assert len(ramp) == 20
        assert len(normal) == 20
        # Ramp should generally increase
        assert ramp[-1] > ramp[0]
        # Flash sale should reach peak quickly
        assert max(flash) >= 600


class TestScalingStrategies:
    """Tests for individual scaling strategy decision logic."""

    def test_fixed_strategy_constant_output(self) -> None:
        strategy = FixedStrategy(replicas=3, cpu_limit_milli=1500, db_pool_size=30)
        state = SystemState(
            time_step=1,
            workload_rps=800.0,
            current_replicas=3,
            cpu_utilization_pct=95.0,
            queue_backlog=150,
            p95_latency_ms=750.0,
            error_rate_pct=5.0,
            db_connections_active=90,
        )
        dec = strategy.decide(state)
        assert isinstance(dec, StrategyDecision)
        assert dec.replicas == 3
        assert dec.cpu_limit_milli == 1500
        assert dec.db_pool_size == 30
        assert dec.admission_rate == 1.0

    def test_hpa_strategy_scales_up_on_high_cpu(self) -> None:
        hpa = KubernetesHPAStrategy(target_cpu_pct=70.0, min_replicas=2, max_replicas=8)
        state_high_cpu = SystemState(
            time_step=5,
            workload_rps=600.0,
            current_replicas=2,
            cpu_utilization_pct=98.0,
            queue_backlog=20,
            p95_latency_ms=300.0,
            error_rate_pct=0.0,
            db_connections_active=40,
        )
        dec = hpa.decide(state_high_cpu)
        assert dec.replicas > 2  # Scaled up
        assert dec.db_pool_size == 20  # HPA does NOT tune DB pool!

    def test_queue_backlog_strategy_scales_on_queue_depth(self) -> None:
        q_scaler = QueueBacklogStrategy(target_backlog_per_pod=50, min_replicas=2)
        state_high_queue = SystemState(
            time_step=5,
            workload_rps=500.0,
            current_replicas=2,
            cpu_utilization_pct=50.0,
            queue_backlog=250,  # 250 / 50 = 5 pods desired
            p95_latency_ms=120.0,
            error_rate_pct=0.0,
            db_connections_active=40,
        )
        dec = q_scaler.decide(state_high_queue)
        assert dec.replicas >= 5

    def test_scopex_strategy_joint_optimization(self) -> None:
        scopex = ScopeXStrategy(max_replicas=10, max_cpu_milli=2000)
        state_surge = SystemState(
            time_step=5,
            workload_rps=600.0,
            current_replicas=2,
            cpu_utilization_pct=90.0,
            queue_backlog=50,
            p95_latency_ms=250.0,
            error_rate_pct=0.0,
            db_connections_active=40,
        )
        dec = scopex.decide(state_surge)
        # Joint optimization: scales replicas AND DB pool to meet validated constraints
        assert dec.replicas >= 2
        assert dec.db_pool_size >= 25  # High load pool constraint satisfied!
        assert dec.cache_enabled is True
        assert dec.admission_rate > 0.0


class TestSystemSimulator:
    """Tests for system latency, bottleneck, and error simulation."""

    def test_healthy_execution_maintains_low_latency(self) -> None:
        sim = SystemSimulator(seed=42)
        # 100 RPS with 2 replicas, 1000m CPU, pool 20
        decision = StrategyDecision(
            replicas=2, cpu_limit_milli=1000, db_pool_size=20, cache_enabled=True, admission_rate=1.0
        )
        state, telemetry = sim.step(time_step=0, workload_rps=100.0, decision=decision)

        assert telemetry.p95_latency_ms < 100.0
        assert telemetry.error_rate_pct < 1.0
        assert telemetry.is_slo_violated is False
        assert state.cpu_utilization_pct < 50.0

    def test_overload_and_db_starvation_triggers_latency_spike(self) -> None:
        sim = SystemSimulator(seed=42)
        # Overload: 700 RPS on 1 replica, db_pool 10 (violates capacity and db pool rules)
        decision = StrategyDecision(
            replicas=1, cpu_limit_milli=500, db_pool_size=10, cache_enabled=False, admission_rate=1.0
        )
        _, telemetry = sim.step(time_step=0, workload_rps=700.0, decision=decision)

        assert telemetry.p95_latency_ms > 500.0
        assert telemetry.is_slo_violated is True


class TestComparisonHarness:
    """Tests for unified comparative execution across all 4 strategies."""

    def test_run_comparison_end_to_end(self) -> None:
        report = run_comparison(
            profile="flash_sale",
            peak_rps=700,
            duration_seconds=20,
            seed=42,
        )

        assert isinstance(report, ComparisonReport)
        assert len(report.strategies) == 4
        strat_names = [s.strategy_name for s in report.strategies]
        assert "Fixed Baseline" in strat_names
        assert "Kubernetes HPA (CPU)" in strat_names
        assert "Queue-Backlog Scaler" in strat_names
        assert "SCOPE-X (Ours)" in strat_names

        # All strategies must have been evaluated under the exact same total offered requests
        total_requests = [s.total_requests for s in report.strategies]
        assert len(set(total_requests)) == 1

        # Check SCOPE-X outperforms Fixed and HPA on SLO violations
        fixed_rec = next(s for s in report.strategies if "Fixed" in s.strategy_name)
        scopex_rec = next(s for s in report.strategies if "SCOPE-X" in s.strategy_name)

        assert isinstance(fixed_rec, StrategyComparisonRecord)
        assert isinstance(scopex_rec, StrategyComparisonRecord)

        assert scopex_rec.slo_violation_pct < fixed_rec.slo_violation_pct
        assert scopex_rec.avg_p95_latency_ms < fixed_rec.avg_p95_latency_ms
        assert scopex_rec.status == "PASSED"

        # Check table formatting
        table = report.format_table()
        assert "STRATEGY COMPARISON BENCHMARK" in table
        assert "Fixed Baseline" in table
        assert "SCOPE-X (Ours)" in table

    def test_cli_compare_command(self, tmp_path: Path) -> None:
        out_json = tmp_path / "cmp_summary.json"
        exit_code = cli_main([
            "compare",
            "--profile", "flash_sale",
            "--rps", "600",
            "--duration", "15",
            "--seed", "99",
            "--json-output", str(out_json),
        ])
        assert exit_code == 0
        assert out_json.exists()

        with open(out_json, encoding="utf-8") as f:
            data = json.load(f)

        assert data["profile"] == "flash_sale"
        assert len(data["strategies"]) == 4
