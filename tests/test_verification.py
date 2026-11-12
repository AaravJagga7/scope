"""Unit and integration tests for formal verification and real testbed validation loop."""

from __future__ import annotations

import z3

from scopex.discovery import (
    DomainBounds,
    SLORequirements,
    VerificationLoop,
)


class TestSLORequirementsAndDomain:
    def test_default_values(self) -> None:
        reqs = SLORequirements()
        assert reqs.max_p95_latency_ms == 500.0
        assert reqs.max_error_rate_pct == 1.0
        assert reqs.max_db_connections == 50.0

        domain = DomainBounds()
        assert domain.min_rps == 50.0
        assert domain.max_rps == 1000.0
        assert domain.min_replicas == 1
        assert domain.max_replicas == 10


class TestVerificationLoopCounterexample:
    def test_find_counterexample_when_permissive(self) -> None:
        loop = VerificationLoop()
        # Overly permissive constraint: permits any RPS with 2 replicas
        candidate = z3.And(loop.rps <= 900.0, loop.replicas >= 2.0)
        ce_config, v_name, r_rule = loop.find_counterexample(candidate)

        assert ce_config is not None
        assert "target_rps" in ce_config
        assert "checkout_replicas" in ce_config
        assert "cache_enabled" in ce_config
        assert v_name is not None
        assert r_rule is not None

    def test_find_counterexample_unsat_when_sound(self) -> None:
        loop = VerificationLoop()
        # Sound restrictive constraint: low RPS, high replicas, cache enabled
        safe_candidate = z3.And(
            loop.rps <= 100.0,
            loop.replicas >= 4.0,
            loop.cache == 1.0,
            loop.rps <= (loop.replicas * 70.0),
        )
        ce_config, _, _ = loop.find_counterexample(safe_candidate)
        assert ce_config is None


class TestRealExperimentExecution:
    def test_execute_real_experiment_returns_metrics(self) -> None:
        loop = VerificationLoop()
        config = {
            "target_rps": 500.0,
            "checkout_replicas": 2,
            "db_pool_size": 20,
            "cache_enabled": False,
            "consumers": 2,
        }
        metrics = loop.execute_real_experiment(config)
        assert "p95_latency_ms" in metrics
        assert "error_rate_pct" in metrics
        assert "db_connections" in metrics
        assert metrics["p95_latency_ms"] > 0


class TestVerificationLoopEndToEnd:
    def test_loop_executes_refinement_cycle(self) -> None:
        loop = VerificationLoop(budget=3)
        summary = loop.run()

        # Acceptance: At least one constraint refined after a counterexample
        assert summary.total_refinements >= 1
        assert summary.total_real_experiments >= 1
        assert len(summary.history) >= 1

        # Check explicit separation in iteration logs
        first_iter = summary.history[0]
        assert first_iter.model_verification_status == "COUNTEREXAMPLE_FOUND"
        assert first_iter.real_validation_status == "VALIDATED_ON_REAL_TESTBED"
        assert first_iter.real_slo_violated is True
        assert first_iter.refinement_clause is not None
        assert summary.final_constraint_str != summary.initial_constraint_str
