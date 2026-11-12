"""Execution Manager for the Adaptive Scaling Decision Engine.

Strictly separate from the recommender (engine.py).  The engine outputs a
Recommendation; the ExecutionManager consumes it and applies it — either as a
dry-run log entry or by calling the real cluster API.

Design principles
-----------------
* The ExecutionManager knows *nothing* about how recommendations are produced.
  It receives a Recommendation and decides whether / how to apply it.
* Dry-run mode is the default.  Live mode must be explicitly enabled.
* Every execution is recorded in an append-only log returned as ExecutionResult.
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import asdict, dataclass, field
from typing import Any

from scopex.decision.engine import Recommendation

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class ExecutionPlan:
    """A Recommendation wrapped with execution metadata before it is applied.

    Attributes
    ----------
    recommendation:
        The Recommendation produced by AdaptiveDecisionEngine.
    dry_run:
        When True the plan is logged but no real cluster change is made.
    planned_at:
        ISO-8601 UTC timestamp when the plan was created.
    operator_override:
        Optional free-text note added by an operator (e.g. "approved by on-call").
    execution_id:
        Monotonically increasing counter assigned at plan creation time.
    """

    recommendation: Recommendation
    dry_run: bool = True
    planned_at: str = field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC).isoformat()
    )
    operator_override: str | None = None
    execution_id: int = 0

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # asdict recurses into dataclasses; convert enum values to str
        d["recommendation"]["recommended_action"]["action_type"] = (
            self.recommendation.recommended_action.action_type.value
        )
        d["recommendation"]["recommended_action"]["safety_status"] = (
            self.recommendation.recommended_action.safety_status.value
        )
        for ca in d["recommendation"]["all_candidates"]:
            ca["action_type"] = str(ca["action_type"])
            ca["safety_status"] = str(ca["safety_status"])
        return d


@dataclass
class ExecutionResult:
    """Outcome recorded after an ExecutionPlan is processed.

    Attributes
    ----------
    execution_id:
        Matches the ExecutionPlan.execution_id.
    success:
        True when the action was applied (or dry-run completed) without error.
    action_type:
        Which action was attempted.
    dry_run:
        Whether this was a dry-run.
    applied_at:
        ISO-8601 UTC timestamp of execution.
    applied_config:
        The configuration values that were (would be) applied.
    error_message:
        Non-empty only on failure.
    notes:
        Additional log notes (e.g. safety rejections, validation warnings).
    """

    execution_id: int
    success: bool
    action_type: str
    dry_run: bool
    applied_at: str
    applied_config: dict[str, Any]
    error_message: str = ""
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def format_summary(self) -> str:
        """Return a human-readable single-paragraph summary."""
        status = "DRY-RUN" if self.dry_run else "APPLIED"
        ok = "✓ SUCCESS" if self.success else "✗ FAILED"
        lines = [
            f"[{ok}] Execution #{self.execution_id} — {status}",
            f"  Action      : {self.action_type}",
            f"  Timestamp   : {self.applied_at}",
            f"  Config      : {self.applied_config}",
        ]
        if self.error_message:
            lines.append(f"  Error       : {self.error_message}")
        for note in self.notes:
            lines.append(f"  Note        : {note}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Execution Manager
# ---------------------------------------------------------------------------


class ExecutionManager:
    """Applies (or dry-runs) an ExecutionPlan against the cluster.

    The ExecutionManager is intentionally decoupled from the
    AdaptiveDecisionEngine.  It does **not** know how recommendations are
    produced; it only knows how to apply them safely.

    Parameters
    ----------
    live:
        When False (default) every plan is a dry-run regardless of the
        ``plan.dry_run`` flag.  Pass True only in production environments.
    apply_fn:
        Optional callable ``(config: dict) -> None`` that pushes a config
        to the real cluster.  Defaults to a no-op logger.  Replace with a
        real implementation (e.g. kubectl patch, Helm upgrade) in production.
    """

    _counter: int = 0

    def __init__(
        self,
        live: bool = False,
        apply_fn: Any = None,
    ) -> None:
        self.live = live
        self._apply_fn = apply_fn or self._default_apply_fn

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def plan(
        self,
        recommendation: Recommendation,
        dry_run: bool = True,
        operator_override: str | None = None,
    ) -> ExecutionPlan:
        """Wrap a Recommendation in an ExecutionPlan.

        Parameters
        ----------
        recommendation:
            Output from AdaptiveDecisionEngine.evaluate_and_recommend().
        dry_run:
            True → log only; False → call the cluster API.
        operator_override:
            Optional approval note.

        Returns
        -------
        ExecutionPlan ready to be passed to execute().
        """
        ExecutionManager._counter += 1
        ep = ExecutionPlan(
            recommendation=recommendation,
            dry_run=dry_run or not self.live,
            operator_override=operator_override,
            execution_id=ExecutionManager._counter,
        )
        logger.info(
            "ExecutionPlan #%d created: action=%s dry_run=%s",
            ep.execution_id,
            recommendation.recommended_action.action_type.value,
            ep.dry_run,
        )
        return ep

    def execute(self, plan: ExecutionPlan) -> ExecutionResult:
        """Apply (or dry-run) an ExecutionPlan.

        If plan.dry_run is True (or self.live is False), no real cluster
        changes are made.  The function always returns an ExecutionResult.

        Parameters
        ----------
        plan:
            The plan created by plan().

        Returns
        -------
        ExecutionResult with success=True unless an unexpected exception occurs.
        """
        rec = plan.recommendation
        action = rec.recommended_action
        notes: list[str] = []

        # Build the target config dict
        cfg: dict[str, Any] = {
            "checkout_replicas": action.target_replicas,
            "cpu_limit_milli": action.target_cpu_limit_milli,
            "db_pool_size": action.target_db_pool_size,
            "cache_enabled": action.target_cache_enabled,
            "admission_rate": action.target_admission_rate,
        }

        # Reject non-HOLD actions that the safety guard already flagged
        from scopex.decision.engine import ActionSafetyStatus  # local import avoids cycle

        if action.safety_status == ActionSafetyStatus.REJECTED:
            msg = (
                f"Cannot execute plan #{plan.execution_id}: "
                f"action '{action.action_type.value}' was rejected by SafetyGuard. "
                f"Reason: {action.rejection_reason or 'unknown'}"
            )
            logger.warning(msg)
            return ExecutionResult(
                execution_id=plan.execution_id,
                success=False,
                action_type=action.action_type.value,
                dry_run=plan.dry_run,
                applied_at=datetime.datetime.now(datetime.UTC).isoformat(),
                applied_config=cfg,
                error_message=msg,
                notes=notes,
            )

        effective_dry_run = plan.dry_run or not self.live
        try:
            if effective_dry_run:
                self._log_dry_run(plan, cfg)
                notes.append("DRY-RUN: configuration logged but not applied to cluster")
            else:
                self._apply_fn(cfg)
                notes.append(f"Configuration applied by ExecutionManager #{plan.execution_id}")
                if plan.operator_override:
                    notes.append(f"Operator override: {plan.operator_override}")

            applied_at = datetime.datetime.now(datetime.UTC).isoformat()
            logger.info(
                "Execution #%d complete: action=%s dry_run=%s success=True",
                plan.execution_id,
                action.action_type.value,
                effective_dry_run,
            )
            return ExecutionResult(
                execution_id=plan.execution_id,
                success=True,
                action_type=action.action_type.value,
                dry_run=effective_dry_run,
                applied_at=applied_at,
                applied_config=cfg,
                notes=notes,
            )

        except Exception as exc:  # noqa: BLE001
            msg = f"Exception during execution #{plan.execution_id}: {exc}"
            logger.exception(msg)
            return ExecutionResult(
                execution_id=plan.execution_id,
                success=False,
                action_type=action.action_type.value,
                dry_run=effective_dry_run,
                applied_at=datetime.datetime.now(datetime.UTC).isoformat(),
                applied_config=cfg,
                error_message=msg,
                notes=notes,
            )

    def apply_decision(
        self,
        recommendation: Recommendation,
        dry_run: bool = True,
        operator_override: str | None = None,
    ) -> ExecutionResult:
        """Convenience wrapper: plan then execute in one call.

        Parameters
        ----------
        recommendation:
            Recommendation from AdaptiveDecisionEngine.
        dry_run:
            True → log only.
        operator_override:
            Optional approval note.

        Returns
        -------
        ExecutionResult.
        """
        plan = self.plan(recommendation, dry_run=dry_run, operator_override=operator_override)
        return self.execute(plan)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _default_apply_fn(config: dict[str, Any]) -> None:
        """Default no-op apply function (logs only; replace in production)."""
        logger.info("apply_fn(config=%s) — replace with real cluster API call", config)

    @staticmethod
    def _log_dry_run(plan: ExecutionPlan, cfg: dict[str, Any]) -> None:
        """Emit a structured dry-run log entry."""
        logger.info(
            "[DRY-RUN] ExecutionPlan #%d would apply: action=%s config=%s reasoning=%s",
            plan.execution_id,
            plan.recommendation.recommended_action.action_type.value,
            cfg,
            plan.recommendation.reasoning[:120] + "..."
            if len(plan.recommendation.reasoning) > 120
            else plan.recommendation.reasoning,
        )
        if plan.operator_override:
            logger.info("[DRY-RUN] Operator override: %s", plan.operator_override)
