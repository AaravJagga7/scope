"""Acquisition functions targeting the SLO boundary with BoTorch.

Standard Bayesian Optimization typically seeks global extrema (e.g. minimizing
latency or maximizing throughput). For SLO boundary discovery, the objective is
identifying the contour or level-set where performance crosses an operational
threshold (e.g. p95 latency = 500 ms).

This module implements:
- :class:`SLOBoundaryAcquisition`: An analytic acquisition function derived from
  the Straddle rule (Bryan et al., 2005) and weighted boundary uncertainty.
  Candidates near the threshold with high epistemic uncertainty are prioritized.
- Selection helper functions for candidate pool scoring and argmax extraction.
"""

from __future__ import annotations

import logging
from typing import Literal

import numpy as np
import torch
from botorch.acquisition.analytic import AnalyticAcquisitionFunction
from botorch.models.model import Model
from botorch.utils.transforms import t_batch_mode_transform

logger = logging.getLogger(__name__)

AcquisitionMethod = Literal["straddle", "weighted_uncertainty"]


class SLOBoundaryAcquisition(AnalyticAcquisitionFunction):
    """BoTorch acquisition function targeting an SLO boundary contour.

    Supports two formulations:
    1. **Straddle** (Bryan et al., 2005; standard level-set estimation):
       .. math::
           \\alpha(x) = \\beta \\cdot \\sigma(x) - |\\mu(x) - T|

       Where points with high posterior standard deviation :math:`\\sigma(x)` and
       posterior mean :math:`\\mu(x)` close to the SLO threshold :math:`T` receive
       the highest acquisition score.

    2. **Weighted Uncertainty**:
       .. math::
           \\alpha(x) = \\sigma(x) \\cdot \\exp\\left( - \\frac{(\\mu(x) - T)^2}{2 \\sigma(x)^2 + \\epsilon} \\right)

    Args:
        model: Trained BoTorch or GPyTorch model supporting `.posterior(X)`.
        threshold: Target performance threshold (e.g. 500.0 ms for p95 latency).
        beta: Trade-off parameter balancing exploration (uncertainty) and
            exploitation (proximity to threshold). Default 1.96 (approx 95% CI).
        method: Formulation to use (``"straddle"`` or ``"weighted_uncertainty"``).
    """

    def __init__(
        self,
        model: Model,
        threshold: float = 500.0,
        beta: float = 1.96,
        method: AcquisitionMethod = "straddle",
    ) -> None:
        super().__init__(model=model)
        self.threshold = float(threshold)
        self.beta = float(beta)
        if method not in ("straddle", "weighted_uncertainty"):
            raise ValueError(f"Unknown method {method!r}. Must be 'straddle' or 'weighted_uncertainty'")
        self.method: AcquisitionMethod = method

    @t_batch_mode_transform(expected_q=1)
    def forward(self, X: torch.Tensor) -> torch.Tensor:
        """Evaluate the acquisition function on design points *X*.

        Args:
            X: Input tensor of shape ``(batch_shape, 1, d)``.

        Returns:
            Acquisition values tensor of shape ``(batch_shape,)``.
        """
        # _mean_and_sigma automatically applies any outcome transforms to return physical units
        mean, sigma = self._mean_and_sigma(X)  # each (batch_shape, 1)
        mean = mean.squeeze(-1)
        sigma = sigma.squeeze(-1)

        diff = torch.abs(mean - self.threshold)

        if self.method == "straddle":
            return self.beta * sigma - diff
        else:
            # Gaussian-weighted uncertainty around the threshold
            var = sigma.pow(2).clamp_min(1e-9)
            weight = torch.exp(-0.5 * (diff.pow(2) / var))
            return sigma * weight


def select_next_candidate_bo(
    model: Model,
    X_pool: torch.Tensor,
    remaining_indices: list[int],
    threshold: float = 500.0,
    beta: float = 1.96,
    method: AcquisitionMethod = "straddle",
) -> int:
    """Select the candidate index from *remaining_indices* maximizing the acquisition function.

    Args:
        model: Fitted BoTorch model.
        X_pool: Tensor of normalized input features for all candidates, shape ``(N, d)``.
        remaining_indices: List of candidate row indices in *X_pool* eligible for selection.
        threshold: Target performance threshold.
        beta: Exploration-exploitation trade-off parameter.
        method: Acquisition formula (``"straddle"`` or ``"weighted_uncertainty"``).

    Returns:
        Index of the chosen candidate within the candidate pool.
    """
    if not remaining_indices:
        raise ValueError("remaining_indices cannot be empty")

    cand_x = X_pool[remaining_indices].unsqueeze(1)  # shape (K, 1, d)
    acq = SLOBoundaryAcquisition(model=model, threshold=threshold, beta=beta, method=method)

    with torch.no_grad():
        scores = acq(cand_x).cpu().numpy()

    best_local_idx = int(np.argmax(scores))
    return remaining_indices[best_local_idx]
