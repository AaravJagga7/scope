"""Tests for OR-Tools CP-SAT microservice configuration optimization and cost model."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scopex.optimisation import (
    ActiveConstraint,
    CheapestConfig,
    CostBreakdown,
    CostModel,
    CPSATConfigOptimizer,
    OptimizationResult,
)
from scripts.optimize_config import main as cli_main


class TestCostModel:
    """Tests for CostModel pricing, serialization, and presets."""

    def test_default_cost_computation(self) -> None:
        model = CostModel(
            replica_base_cost_hourly=0.05,
            cpu_core_cost_hourly=0.04,
            db_conn_cost_hourly=0.002,
            cache_cost_hourly=0.02,
            dropped_rps_penalty_hourly=0.50,
        )

        # 2 replicas, 1000m CPU (1.0 core), 25 DB pool, cache=True, workload 500, admitted 500
        cost = model.compute_cost(
            replicas=2,
            cpu_limit_milli=1000,
            db_pool_size=25,
            cache_enabled=True,
            workload_rps=500,
            admitted_rps=500,
        )

        assert isinstance(cost, CostBreakdown)
        assert cost.replica_base_cost == pytest.approx(0.10)  # 2 * 0.05
        assert cost.cpu_cost == pytest.approx(0.08)          # (2 * 1.0) * 0.04
        assert cost.db_pool_cost == pytest.approx(0.10)      # (2 * 25) * 0.002
        assert cost.cache_cost == pytest.approx(0.02)
        assert cost.penalty_cost == pytest.approx(0.0)
        assert cost.total_hourly_cost == pytest.approx(0.30)

    def test_shedding_penalty(self) -> None:
        model = CostModel(dropped_rps_penalty_hourly=1.0)
        cost = model.compute_cost(
            replicas=1,
            cpu_limit_milli=500,
            db_pool_size=10,
            cache_enabled=False,
            workload_rps=1000,
            admitted_rps=800,  # 200 RPS shed
        )
        assert cost.penalty_cost == pytest.approx(200.0)
        assert cost.total_hourly_cost > 200.0

    def test_dict_and_file_serialization(self, tmp_path: Path) -> None:
        model = CostModel(
            replica_base_cost_hourly=0.08,
            cpu_core_cost_hourly=0.06,
        )
        d = model.to_dict()
        assert d["replica_base_cost_hourly"] == 0.08
        assert d["cpu_core_cost_hourly"] == 0.06

        json_file = tmp_path / "cost_model.json"
        with open(json_file, "w", encoding="utf-8") as f:
            json.dump(d, f)

        loaded = CostModel.from_json(json_file)
        assert loaded.replica_base_cost_hourly == 0.08
        assert loaded.cpu_core_cost_hourly == 0.06

    def test_presets(self) -> None:
        std = CostModel.standard_cloud()
        strict = CostModel.strict_availability()
        saving = CostModel.cost_saving()

        assert strict.dropped_rps_penalty_hourly > std.dropped_rps_penalty_hourly
        assert saving.dropped_rps_penalty_hourly < std.dropped_rps_penalty_hourly


class TestCPSATConfigOptimizer:
    """Tests for the CP-SAT constraint optimization engine."""

    def test_low_workload_optimization(self) -> None:
        optimizer = CPSATConfigOptimizer()
        result = optimizer.optimize(workload_rps=100)

        assert isinstance(result, OptimizationResult)
        assert result.status in ("OPTIMAL", "FEASIBLE")
        c = result.cheapest_config
        assert isinstance(c, CheapestConfig)
        assert c.checkout_replicas >= 1
        assert c.admission_rate_pct == 100
        assert c.admitted_rps == 100
        assert c.shed_rps == 0
        # Under 300 RPS, cache can be disabled to save cost
        assert c.cache_enabled is False

        # Active constraints check
        binding_names = [ac.name for ac in result.binding_constraints]
        assert "Admission Control Upper Bound" in binding_names
        assert "Service CPU Throughput Capacity" in binding_names

    def test_medium_workload_cache_and_db_tightening(self) -> None:
        optimizer = CPSATConfigOptimizer()
        result = optimizer.optimize(workload_rps=500)

        c = result.cheapest_config
        assert c.admission_rate_pct == 100
        # Validated rule: traffic > 300 RPS requires cache
        assert c.cache_enabled is True
        # Validated rule: traffic > 400 RPS requires db_pool_size >= 25
        assert c.db_pool_size >= 25

        binding_names = [ac.name for ac in result.binding_constraints]
        assert "Cache Requirement Invariant" in binding_names
        assert "High-Load DB Pool Constraint" in binding_names

    def test_high_workload_horizontal_scaling(self) -> None:
        optimizer = CPSATConfigOptimizer()
        result = optimizer.optimize(workload_rps=1200)

        c = result.cheapest_config
        # Replicas scale horizontally to handle 1200 RPS
        assert c.checkout_replicas >= 3
        assert c.cache_enabled is True
        assert c.admission_rate_pct == 100
        assert result.cost_breakdown.total_hourly_cost > 0.0

    def test_active_constraints_details_and_report(self) -> None:
        optimizer = CPSATConfigOptimizer()
        result = optimizer.optimize(workload_rps=500)

        # Check ActiveConstraint structure
        for ac in result.active_constraints:
            assert isinstance(ac, ActiveConstraint)
            assert ac.name
            assert ac.category in ("capacity", "database", "caching", "admission", "resource_bound")
            assert ac.description
            assert ac.bound_value >= 0
            assert ac.actual_value >= 0
            assert ac.slack >= 0
            assert isinstance(ac.is_binding, bool)

        report = result.format_report()
        assert "CP-SAT CONFIGURATION OPTIMIZATION REPORT" in report
        assert "CHEAPEST FEASIBLE CONFIGURATION" in report
        assert "ESTIMATED COST BREAKDOWN" in report
        assert "ACTIVE & BINDING CONSTRAINTS EXPLANATION" in report
        assert "SYNTHESIS EXPLANATION" in report
        assert result.explanation in report

    def test_load_shedding_under_tight_capacity(self) -> None:
        # Heavily restricted cluster: max 1 replica, max 500m CPU (capacity ~87 RPS cached)
        optimizer = CPSATConfigOptimizer(
            max_replicas=1,
            max_cpu_milli=500,
            cost_model=CostModel(dropped_rps_penalty_hourly=0.01),  # cheap shedding
        )
        result = optimizer.optimize(workload_rps=1000)

        c = result.cheapest_config
        # Should shed traffic because cluster cannot physically serve 1000 RPS
        assert c.shed_rps > 0
        assert c.admission_rate_pct < 100

        binding_names = [ac.name for ac in result.binding_constraints]
        assert "Admission Control Upper Bound" in binding_names

    def test_invalid_arguments_raise_error(self) -> None:
        optimizer = CPSATConfigOptimizer()
        with pytest.raises(ValueError, match="workload_rps must be positive"):
            optimizer.optimize(workload_rps=-10)

        with pytest.raises(ValueError, match="min_admission_rate must be in"):
            optimizer.optimize(workload_rps=100, min_admission_rate=1.5)


class TestOptimizationCLI:
    """Tests for the scripts/optimize_config.py CLI."""

    def test_cli_execution_and_json_output(self, tmp_path: Path) -> None:
        out_json = tmp_path / "optimal.json"
        exit_code = cli_main([
            "--workload", "400",
            "--json-output", str(out_json),
        ])
        assert exit_code == 0
        assert out_json.exists()

        with open(out_json, encoding="utf-8") as f:
            data = json.load(f)

        assert data["workload_rps"] == 400
        assert "cheapest_config" in data
        assert "active_constraints" in data
        assert "cost_breakdown" in data
        assert "explanation" in data

    def test_cli_presets(self, capsys: pytest.CaptureFixture[str]) -> None:
        exit_code = cli_main([
            "--workload", "250",
            "--preset", "strict_availability",
            "--json",
        ])
        assert exit_code == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["workload_rps"] == 250
        assert data["cheapest_config"]["admitted_rps"] == 250
