"""Decision module: policy decision engines and comparison strategies."""

from scopex.decision.engine import (
    ActionSafetyStatus,
    ActionType,
    AdaptiveDecisionEngine,
    CandidateAction,
    LiveMetrics,
    Recommendation,
    SafetyGuard,
)
from scopex.decision.executor import ExecutionManager, ExecutionPlan, ExecutionResult
from scopex.decision.harness import (
    ComparisonHarness,
    ComparisonReport,
    StrategyComparisonRecord,
    SystemSimulator,
    TimeStepTelemetry,
    WorkloadTraceGenerator,
    run_comparison,
)
from scopex.decision.strategies import (
    FixedStrategy,
    KubernetesHPAStrategy,
    QueueBacklogStrategy,
    ScalingStrategy,
    ScopeXStrategy,
    StrategyDecision,
    SystemState,
)

__all__ = [
    # Engine (Task 17)
    "ActionSafetyStatus",
    "ActionType",
    "AdaptiveDecisionEngine",
    "CandidateAction",
    "ExecutionManager",
    "ExecutionPlan",
    "ExecutionResult",
    "LiveMetrics",
    "Recommendation",
    "SafetyGuard",
    # Comparison harness (Task 16)
    "ComparisonHarness",
    "ComparisonReport",
    "FixedStrategy",
    "KubernetesHPAStrategy",
    "QueueBacklogStrategy",
    "ScalingStrategy",
    "ScopeXStrategy",
    "StrategyComparisonRecord",
    "StrategyDecision",
    "SystemSimulator",
    "SystemState",
    "TimeStepTelemetry",
    "WorkloadTraceGenerator",
    "run_comparison",
]
