"""Decision module: policy decision engines and comparison strategies."""

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
