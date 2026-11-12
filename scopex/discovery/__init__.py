"""Discovery module: GP surrogate, active learning, and interpretable constraint discovery."""

from scopex.discovery.acquisition import (
    SLOBoundaryAcquisition,
    select_next_candidate_bo,
)
from scopex.discovery.constraints import (
    Constraint,
    evaluate_and_rank_constraints,
    format_constraints_report,
    learn_constraints,
    learn_decision_tree_constraints,
    learn_sparse_regression_constraints,
)
from scopex.discovery.evaluation import (
    compute_metrics,
    plot_active_learning_efficiency,
    plot_predicted_vs_actual,
    plot_uncertainty_vs_traffic,
)
from scopex.discovery.selection import (
    ActiveLearningBenchmark,
    BenchmarkSummary,
    StepRecord,
    StrategyResult,
    StrategyType,
)
from scopex.discovery.surrogate import (
    PROFILE_ENCODER,
    SUPPORTED_TARGETS,
    GPSurrogate,
    build_feature_matrix,
)

__all__ = [
    "GPSurrogate",
    "build_feature_matrix",
    "PROFILE_ENCODER",
    "SUPPORTED_TARGETS",
    "compute_metrics",
    "plot_predicted_vs_actual",
    "plot_uncertainty_vs_traffic",
    "plot_active_learning_efficiency",
    "SLOBoundaryAcquisition",
    "select_next_candidate_bo",
    "ActiveLearningBenchmark",
    "BenchmarkSummary",
    "StepRecord",
    "StrategyResult",
    "StrategyType",
    "Constraint",
    "learn_constraints",
    "learn_decision_tree_constraints",
    "learn_sparse_regression_constraints",
    "evaluate_and_rank_constraints",
    "format_constraints_report",
]
