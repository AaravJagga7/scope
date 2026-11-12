"""Tests for the Adaptive Scaling Decision Engine (Task 17).

Covers:
- SafetyGuard: DB ceiling enforcement, replica limits, pool constraints.
- AdaptiveDecisionEngine: flash-sale acceptance scenario, candidate generation.
- ExecutionManager: dry-run, plan wrapping, rejected-action guard.
- CLI 'decide' subcommand: smoke test.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Path setup – allow running directly without install
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).parent.parent))

from scopex.decision.engine import (
    ActionSafetyStatus,
    ActionType,
    AdaptiveDecisionEngine,
    CandidateAction,
    LiveMetrics,
    Recommendation,
    SafetyGuard,
)
from scopex.decision.executor import ExecutionManager, ExecutionPlan

# ===========================================================================
# Helpers
# ===========================================================================


def _make_candidate(
    action: ActionType,
    *,
    replicas: int = 4,
    db_pool: int = 30,
    admission_rate: float = 1.0,
    p95: float = 350.0,
    err: float = 0.3,
) -> CandidateAction:
    """Build a minimal CandidateAction for safety-guard unit tests."""
    return CandidateAction(
        action_type=action,
        description=f"Test candidate: {action.value}",
        target_replicas=replicas,
        target_cpu_limit_milli=1000,
        target_db_pool_size=db_pool,
        target_cache_enabled=True,
        target_admission_rate=admission_rate,
        predicted_p95_ms=p95,
        predicted_p95_std=15.0,
        predicted_error_rate_pct=err,
        safety_status=ActionSafetyStatus.SAFE,
    )


def _flash_sale_metrics(
    *,
    replicas: int = 4,
    db_pool: int = 48,
    rps: float = 700.0,
    cpu: float = 95.0,
    db_limit: int = 200,
) -> LiveMetrics:
    """High-stress flash-sale scenario with DB connections near ceiling."""
    return LiveMetrics(
        workload_rps=rps,
        cpu_utilization_pct=cpu,
        p95_latency_ms=420.0,
        error_rate_pct=0.5,
        current_replicas=replicas,
        current_db_pool_size=db_pool,
        db_connection_limit=db_limit,
        profile="flash_sale",
    )


def _normal_metrics(
    *,
    replicas: int = 2,
    db_pool: int = 20,
    rps: float = 300.0,
    cpu: float = 65.0,
) -> LiveMetrics:
    """Normal load with plenty of DB headroom."""
    return LiveMetrics(
        workload_rps=rps,
        cpu_utilization_pct=cpu,
        p95_latency_ms=180.0,
        error_rate_pct=0.1,
        current_replicas=replicas,
        current_db_pool_size=db_pool,
        db_connection_limit=200,
        profile="normal",
    )


# ===========================================================================
# TestSafetyGuard
# ===========================================================================


class TestSafetyGuard:
    """Unit tests for SafetyGuard constraint enforcement."""

    def test_add_replicas_rejected_when_db_near_limit(self) -> None:
        """ACCEPTANCE CRITERION: flash sale + high CPU + DB near limit → ADD_REPLICAS rejected.

        Scenario: 4 replicas, db_pool=48 → total_conns = 4*48 = 192.
        Scaling to 5 replicas → 5*48 = 240 > 200 (ceiling) → REJECTED.
        """
        guard = SafetyGuard(max_db_connections=200)
        # ADD_REPLICAS proposes 5 replicas keeping same pool size
        candidate = _make_candidate(ActionType.ADD_REPLICAS, replicas=5, db_pool=48)
        metrics = _flash_sale_metrics(replicas=4, db_pool=48)

        is_safe, reason, violated = guard.evaluate(candidate, metrics)

        assert not is_safe, "ADD_REPLICAS must be rejected when it breaches DB ceiling"
        assert reason is not None
        assert "240" in reason, "Reason should cite the resulting connection count (5*48=240)"
        assert "200" in reason, "Reason should cite the DB limit"
        assert "Declined to add Checkout replicas" in reason
        assert any("total_db_connections" in v for v in violated)

    def test_add_replicas_accepted_when_db_has_headroom(self) -> None:
        """Normal load, small pool → replica scale-out is safe."""
        guard = SafetyGuard(max_db_connections=200)
        # 2 → 3 replicas, pool=20 → 3*20 = 60 << 200
        candidate = _make_candidate(ActionType.ADD_REPLICAS, replicas=3, db_pool=20)
        metrics = _normal_metrics(replicas=2, db_pool=20)

        is_safe, reason, violated = guard.evaluate(candidate, metrics)

        assert is_safe, f"ADD_REPLICAS should be safe with headroom. Reason: {reason}"
        assert reason is None
        assert violated == []

    def test_high_load_db_floor_constraint_enforced(self) -> None:
        """Validated rule: db_pool_size ≤ 25 AND rps > 400 → SLO violation."""
        guard = SafetyGuard(max_db_connections=200)
        # Pool = 20 (< 25), admitted RPS = 700 * 1.0 = 700 (> 400)
        candidate = _make_candidate(ActionType.HOLD, replicas=3, db_pool=20, admission_rate=1.0)
        metrics = _flash_sale_metrics(rps=700.0)

        is_safe, reason, violated = guard.evaluate(candidate, metrics)

        assert not is_safe
        assert "db_pool_size" in reason.lower() or "25" in reason
        assert "400" in reason or "RPS" in reason
        assert any("SLO_VIOLATION" in v for v in violated)

    def test_replica_ceiling_enforced(self) -> None:
        """Replica count above cluster ceiling is rejected."""
        guard = SafetyGuard(max_replicas=10)
        candidate = _make_candidate(ActionType.ADD_REPLICAS, replicas=11, db_pool=10)
        metrics = _normal_metrics()

        is_safe, reason, _ = guard.evaluate(candidate, metrics)

        assert not is_safe
        assert "11" in reason

    def test_replica_floor_enforced(self) -> None:
        """Replica count below minimum baseline is rejected."""
        guard = SafetyGuard(min_replicas=1)
        candidate = _make_candidate(ActionType.HOLD, replicas=0, db_pool=20)
        metrics = _normal_metrics()

        is_safe, reason, _ = guard.evaluate(candidate, metrics)

        assert not is_safe
        assert "0" in reason

    def test_admission_rate_floor_enforced(self) -> None:
        """Admission rate below 50% floor is rejected."""
        guard = SafetyGuard(min_admission_rate=0.50)
        candidate = _make_candidate(
            ActionType.LIMIT_TRAFFIC, replicas=4, db_pool=30, admission_rate=0.30
        )
        metrics = _flash_sale_metrics()

        is_safe, reason, _ = guard.evaluate(candidate, metrics)

        assert not is_safe
        assert "30%" in reason or "0.30" in reason or "50" in reason

    def test_db_pool_ceiling_enforced(self) -> None:
        """DB pool size above per-pod maximum is rejected."""
        guard = SafetyGuard(max_db_pool_size=150)
        candidate = _make_candidate(ActionType.INCREASE_DB_CAPACITY, replicas=2, db_pool=200)
        metrics = _normal_metrics()

        is_safe, reason, _ = guard.evaluate(candidate, metrics)

        assert not is_safe
        assert "200" in reason

    def test_hold_action_always_safe_in_normal_state(self) -> None:
        """HOLD at current pool size and replicas should always pass safety guard."""
        guard = SafetyGuard()
        candidate = _make_candidate(ActionType.HOLD, replicas=3, db_pool=30)
        metrics = _normal_metrics(replicas=3, db_pool=30, rps=300.0)

        is_safe, _, _ = guard.evaluate(candidate, metrics)

        assert is_safe


# ===========================================================================
# TestAdaptiveDecisionEngine
# ===========================================================================


class TestAdaptiveDecisionEngine:
    """Integration tests for AdaptiveDecisionEngine."""

    def setup_method(self) -> None:
        self.engine = AdaptiveDecisionEngine()

    def test_flash_sale_db_limit_scenario(self) -> None:
        """ACCEPTANCE CRITERION (end-to-end).

        In a flash sale where CPU is high but DB is near its limit, the engine
        declines to add Checkout replicas and explains why.
        """
        metrics = _flash_sale_metrics(replicas=4, db_pool=48, cpu=95.0, db_limit=200)
        rec = self.engine.evaluate_and_recommend(metrics)

        assert isinstance(rec, Recommendation)

        # Find the ADD_REPLICAS candidate
        add_rep = next(
            (c for c in rec.all_candidates if c.action_type == ActionType.ADD_REPLICAS),
            None,
        )
        assert add_rep is not None, "ADD_REPLICAS should always be a candidate"
        assert add_rep.safety_status == ActionSafetyStatus.REJECTED, (
            "ADD_REPLICAS must be REJECTED when replicas×pool > db_limit"
        )
        assert add_rep.rejection_reason is not None
        assert "connection" in add_rep.rejection_reason.lower() or "db" in add_rep.rejection_reason.lower(), (
            f"Rejection reason should mention DB connections, got: {add_rep.rejection_reason}"
        )

        # Recommended action must NOT be ADD_REPLICAS
        assert rec.recommended_action.action_type != ActionType.ADD_REPLICAS, (
            f"Engine must not recommend ADD_REPLICAS when DB ceiling is breached, "
            f"got: {rec.recommended_action.action_type.value}"
        )

        # Reasoning must explain the DB ceiling
        report = rec.format_report()
        assert "REJECTED" in report, "Report should show REJECTED verdict for ADD_REPLICAS"
        assert len(rec.reasoning) > 100, "Reasoning should be a substantive explanation"

    def test_recommendation_has_all_four_candidates(self) -> None:
        """Engine always evaluates all four action types."""
        metrics = _normal_metrics()
        rec = self.engine.evaluate_and_recommend(metrics)

        action_types = {c.action_type for c in rec.all_candidates}
        assert ActionType.ADD_REPLICAS in action_types
        assert ActionType.LIMIT_TRAFFIC in action_types
        assert ActionType.INCREASE_DB_CAPACITY in action_types
        assert ActionType.HOLD in action_types

    def test_hold_chosen_when_all_else_rejected(self) -> None:
        """When no positive action is safe, HOLD must be the recommendation."""
        # Extreme DB ceiling (10) forces every action with ≥2 replicas to be rejected
        # We also set a tiny pool so high-load constraint fires for HOLD at high RPS.
        # Use a lower RPS so HOLD itself passes, but ADD_REPLICAS is impossible.
        metrics = LiveMetrics(
            workload_rps=100.0,  # low enough to avoid high-load pool constraint on HOLD
            cpu_utilization_pct=30.0,
            p95_latency_ms=120.0,
            error_rate_pct=0.1,
            current_replicas=1,
            current_db_pool_size=5,
            db_connection_limit=5,  # 1*5=5 exactly at limit → no room to add replicas
            profile="normal",
        )
        rec = self.engine.evaluate_and_recommend(metrics)

        assert rec.recommended_action.action_type == ActionType.HOLD, (
            f"Expected HOLD when DB is fully saturated, got: {rec.recommended_action.action_type}"
        )

    def test_limit_traffic_viable_when_add_replicas_blocked(self) -> None:
        """Under DB pressure, LIMIT_TRAFFIC should be a viable alternative."""
        metrics = _flash_sale_metrics(replicas=4, db_pool=48, cpu=95.0, db_limit=200)
        rec = self.engine.evaluate_and_recommend(metrics)

        limit_traffic = next(
            (c for c in rec.all_candidates if c.action_type == ActionType.LIMIT_TRAFFIC),
            None,
        )
        assert limit_traffic is not None, "LIMIT_TRAFFIC must always be a candidate"
        # It may be safe or the recommended action
        if limit_traffic.safety_status == ActionSafetyStatus.SAFE:
            assert (
                rec.recommended_action.action_type
                in {ActionType.LIMIT_TRAFFIC, ActionType.INCREASE_DB_CAPACITY, ActionType.HOLD}
            )

    def test_recommendation_to_dict_serialisable(self) -> None:
        """Recommendation.to_dict() should be JSON-serialisable."""
        import json

        metrics = _flash_sale_metrics()
        rec = self.engine.evaluate_and_recommend(metrics)
        d = rec.to_dict()
        # Should not raise
        serialised = json.dumps(d)
        assert len(serialised) > 100

    def test_format_report_contains_key_sections(self) -> None:
        """format_report() must include all required sections."""
        metrics = _flash_sale_metrics()
        rec = self.engine.evaluate_and_recommend(metrics)
        report = rec.format_report()

        assert "RECOMMENDED ACTION" in report
        assert "EVALUATED CANDIDATE ACTIONS" in report
        assert "REASONING" in report
        assert "SAFE" in report or "REJECTED" in report

    def test_live_metrics_auto_computes_total_db_conns(self) -> None:
        """LiveMetrics.__post_init__ should compute total_db_connections automatically."""
        m = LiveMetrics(
            workload_rps=400.0,
            cpu_utilization_pct=60.0,
            p95_latency_ms=200.0,
            error_rate_pct=0.2,
            current_replicas=3,
            current_db_pool_size=25,
        )
        assert m.total_db_connections == 75  # 3 × 25

    def test_live_metrics_explicit_total_db_conns(self) -> None:
        """When total_db_connections is set explicitly it should not be overridden."""
        m = LiveMetrics(
            workload_rps=400.0,
            cpu_utilization_pct=60.0,
            p95_latency_ms=200.0,
            error_rate_pct=0.2,
            current_replicas=3,
            current_db_pool_size=25,
            total_db_connections=999,
        )
        assert m.total_db_connections == 999

    def test_normal_load_add_replicas_accepted(self) -> None:
        """Under low load with ample DB headroom ADD_REPLICAS should be safe."""
        metrics = _normal_metrics(replicas=2, db_pool=20, rps=300.0)
        rec = self.engine.evaluate_and_recommend(metrics)

        add_rep = next(
            c for c in rec.all_candidates if c.action_type == ActionType.ADD_REPLICAS
        )
        assert add_rep.safety_status == ActionSafetyStatus.SAFE, (
            "ADD_REPLICAS should be safe when 3×20=60 << 200"
        )

    def test_declined_actions_summary_populated(self) -> None:
        """Declined actions summary should list all rejected actions."""
        metrics = _flash_sale_metrics(replicas=4, db_pool=48)
        rec = self.engine.evaluate_and_recommend(metrics)

        assert isinstance(rec.declined_actions_summary, dict)
        # ADD_REPLICAS must appear in declined summary
        assert ActionType.ADD_REPLICAS.value in rec.declined_actions_summary


# ===========================================================================
# TestExecutionManager
# ===========================================================================


def _make_recommendation(action_type: ActionType = ActionType.HOLD) -> Recommendation:
    """Build a minimal Recommendation for executor tests."""
    metrics = _normal_metrics()
    candidate = CandidateAction(
        action_type=action_type,
        description=f"Test: {action_type.value}",
        target_replicas=3,
        target_cpu_limit_milli=1000,
        target_db_pool_size=25,
        target_cache_enabled=True,
        target_admission_rate=1.0,
        predicted_p95_ms=200.0,
        predicted_p95_std=10.0,
        predicted_error_rate_pct=0.2,
        safety_status=ActionSafetyStatus.SAFE,
    )
    return Recommendation(
        metrics=metrics,
        recommended_action=candidate,
        all_candidates=[candidate],
        reasoning="Test reasoning text with sufficient length to be meaningful.",
        declined_actions_summary={},
        safety_guard_summary="All checks passed.",
    )


def _make_rejected_recommendation() -> Recommendation:
    """Build a Recommendation whose recommended_action is REJECTED."""
    metrics = _flash_sale_metrics()
    candidate = CandidateAction(
        action_type=ActionType.ADD_REPLICAS,
        description="Test rejected candidate",
        target_replicas=5,
        target_cpu_limit_milli=1000,
        target_db_pool_size=48,
        target_cache_enabled=True,
        target_admission_rate=1.0,
        predicted_p95_ms=380.0,
        predicted_p95_std=20.0,
        predicted_error_rate_pct=0.4,
        safety_status=ActionSafetyStatus.REJECTED,
        rejection_reason="Exceeds DB connection limit",
    )
    return Recommendation(
        metrics=metrics,
        recommended_action=candidate,
        all_candidates=[candidate],
        reasoning="Test reasoning.",
        declined_actions_summary={ActionType.ADD_REPLICAS.value: "DB ceiling"},
        safety_guard_summary="ADD_REPLICAS rejected.",
    )


class TestExecutionManager:
    """Unit tests for ExecutionManager (separate from AdaptiveDecisionEngine)."""

    def test_dry_run_does_not_call_apply_fn(self) -> None:
        """In dry-run mode the apply_fn must never be called."""
        apply_fn = MagicMock()
        executor = ExecutionManager(live=True, apply_fn=apply_fn)

        rec = _make_recommendation()
        result = executor.apply_decision(rec, dry_run=True)

        apply_fn.assert_not_called()
        assert result.dry_run is True
        assert result.success is True
        assert "DRY-RUN" in result.notes[0]

    def test_live_mode_calls_apply_fn(self) -> None:
        """In live mode without dry_run flag, apply_fn must be called once."""
        apply_fn = MagicMock()
        executor = ExecutionManager(live=True, apply_fn=apply_fn)

        rec = _make_recommendation()
        result = executor.apply_decision(rec, dry_run=False)

        apply_fn.assert_called_once()
        called_config = apply_fn.call_args[0][0]
        assert "checkout_replicas" in called_config
        assert result.success is True
        assert result.dry_run is False

    def test_plan_wraps_recommendation(self) -> None:
        """plan() returns an ExecutionPlan containing the original Recommendation."""
        executor = ExecutionManager()
        rec = _make_recommendation()
        plan = executor.plan(rec, dry_run=True)

        assert isinstance(plan, ExecutionPlan)
        assert plan.recommendation is rec
        assert plan.dry_run is True
        assert plan.execution_id > 0

    def test_execution_id_increments(self) -> None:
        """Each plan() call should increment the execution ID."""
        executor = ExecutionManager()
        rec = _make_recommendation()
        p1 = executor.plan(rec, dry_run=True)
        p2 = executor.plan(rec, dry_run=True)

        assert p2.execution_id == p1.execution_id + 1

    def test_rejected_action_is_not_applied(self) -> None:
        """execute() on a plan with a REJECTED action must return success=False."""
        apply_fn = MagicMock()
        executor = ExecutionManager(live=True, apply_fn=apply_fn)

        rec = _make_rejected_recommendation()
        result = executor.apply_decision(rec, dry_run=False)

        apply_fn.assert_not_called()
        assert result.success is False
        assert result.error_message  # should have an explanation
        assert "rejected" in result.error_message.lower() or "SafetyGuard" in result.error_message

    def test_operator_override_recorded_in_notes(self) -> None:
        """operator_override text should appear in result notes."""
        apply_fn = MagicMock()
        executor = ExecutionManager(live=True, apply_fn=apply_fn)

        rec = _make_recommendation()
        result = executor.apply_decision(
            rec, dry_run=False, operator_override="Approved by on-call engineer"
        )

        assert any("Approved by on-call" in note for note in result.notes)

    def test_format_summary_contains_key_fields(self) -> None:
        """format_summary() must include action type and execution ID."""
        executor = ExecutionManager()
        rec = _make_recommendation(ActionType.LIMIT_TRAFFIC)
        result = executor.apply_decision(rec, dry_run=True)

        summary = result.format_summary()
        assert "limit_traffic" in summary
        assert str(result.execution_id) in summary
        assert "DRY-RUN" in summary

    def test_apply_fn_exception_captured(self) -> None:
        """If apply_fn raises, ExecutionResult should report success=False with error_message."""

        def bad_apply(config):  # noqa: ANN001
            raise RuntimeError("Cluster API unavailable")

        executor = ExecutionManager(live=True, apply_fn=bad_apply)
        rec = _make_recommendation()
        result = executor.apply_decision(rec, dry_run=False)

        assert result.success is False
        assert "Cluster API unavailable" in result.error_message

    def test_dry_run_forced_when_live_is_false(self) -> None:
        """Even if dry_run=False, live=False forces dry-run mode."""
        apply_fn = MagicMock()
        executor = ExecutionManager(live=False, apply_fn=apply_fn)

        rec = _make_recommendation()
        result = executor.apply_decision(rec, dry_run=False)

        apply_fn.assert_not_called()
        assert result.dry_run is True

    def test_to_dict_is_serialisable(self) -> None:
        """ExecutionResult.to_dict() should be JSON-serialisable."""
        import json

        executor = ExecutionManager()
        rec = _make_recommendation()
        result = executor.apply_decision(rec, dry_run=True)
        d = result.to_dict()
        json.dumps(d)  # must not raise


# ===========================================================================
# TestCLIDecide
# ===========================================================================


class TestCLIDecide:
    """Smoke tests for the 'scopex decide' CLI subcommand."""

    def test_decide_subcommand_smoke(self) -> None:
        """CLI smoke: decide subcommand should return exit code 0."""
        from scopex.cli import main

        exit_code = main(
            [
                "decide",
                "--profile", "flash_sale",
                "--rps", "700",
                "--replicas", "4",
                "--db-pool", "48",
                "--cpu-util", "95",
            ]
        )
        assert exit_code == 0

    def test_decide_json_output(self, capsys: pytest.CaptureFixture) -> None:
        """CLI --json flag should emit valid JSON with recommendation and execution keys."""
        import json

        from scopex.cli import main

        main(
            [
                "decide",
                "--profile", "flash_sale",
                "--rps", "700",
                "--replicas", "4",
                "--db-pool", "48",
                "--cpu-util", "95",
                "--json",
            ]
        )
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "recommendation" in data
        assert "execution" in data
        assert "recommended_action" in data["recommendation"]

    def test_decide_add_replicas_rejected_in_output(self, capsys: pytest.CaptureFixture) -> None:
        """CLI output should show ADD_REPLICAS as REJECTED in flash-sale scenario."""
        from scopex.cli import main

        main(
            [
                "decide",
                "--profile", "flash_sale",
                "--rps", "700",
                "--replicas", "4",
                "--db-pool", "48",
                "--cpu-util", "95",
            ]
        )
        captured = capsys.readouterr()
        assert "REJECTED" in captured.out

    def test_decide_normal_scenario_smoke(self) -> None:
        """CLI smoke under normal load."""
        from scopex.cli import main

        exit_code = main(
            [
                "decide",
                "--profile", "normal",
                "--rps", "300",
                "--replicas", "2",
                "--db-pool", "20",
                "--cpu-util", "55",
            ]
        )
        assert exit_code == 0


# ===========================================================================
# TestModuleExports
# ===========================================================================


class TestModuleExports:
    """Verify that all Task 17 symbols are importable from scopex.decision."""

    def test_engine_classes_exported(self) -> None:
        from scopex.decision import (  # noqa: F401
            ActionSafetyStatus,
            ActionType,
            AdaptiveDecisionEngine,
            CandidateAction,
            LiveMetrics,
            Recommendation,
            SafetyGuard,
        )

    def test_executor_classes_exported(self) -> None:
        from scopex.decision import ExecutionManager, ExecutionPlan, ExecutionResult  # noqa: F401

    def test_task16_classes_still_exported(self) -> None:
        """Existing Task 16 exports must not be broken by Task 17 additions."""
        from scopex.decision import (  # noqa: F401
            ComparisonHarness,
            ComparisonReport,
            FixedStrategy,
            KubernetesHPAStrategy,
            QueueBacklogStrategy,
            ScalingStrategy,
            ScopeXStrategy,
            StrategyComparisonRecord,
            StrategyDecision,
            SystemSimulator,
            SystemState,
            TimeStepTelemetry,
            WorkloadTraceGenerator,
            run_comparison,
        )
