"""Discovery module: GP surrogate and active learning for constraint discovery."""

from scopex.discovery.evaluation import (
    compute_metrics,
    plot_predicted_vs_actual,
    plot_uncertainty_vs_traffic,
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
]
