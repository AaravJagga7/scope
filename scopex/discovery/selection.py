"""Experiment selection engine comparing Bayesian Optimization, Random Search, and Grid Search.

Evaluates how many experiments each strategy requires to accurately identify the
SLO boundary (e.g. p95 latency <= 500 ms) on an independent held-out test set.
"""

from __future__ import annotations

import enum
import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
import torch
from botorch.fit import fit_gpytorch_mll
from botorch.models import SingleTaskGP
from botorch.models.transforms.outcome import Standardize
from gpytorch.mlls import ExactMarginalLogLikelihood
from sklearn.metrics import accuracy_score, f1_score, r2_score

from scopex.discovery.acquisition import AcquisitionMethod, select_next_candidate_bo
from scopex.discovery.surrogate import build_feature_matrix

logger = logging.getLogger(__name__)


class StrategyType(enum.StrEnum):
    BO = "bo"
    RANDOM = "random"
    GRID = "grid"


@dataclass
class StepRecord:
    step: int
    n_experiments: int
    accuracy: float
    f1: float
    rmse: float
    r2: float
    selected_idx: int


@dataclass
class StrategyResult:
    strategy: str
    history: list[StepRecord] = field(default_factory=list)
    experiments_to_target: int | None = None
    final_accuracy: float = 0.0
    final_rmse: float = 0.0
    final_r2: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "experiments_to_target": self.experiments_to_target,
            "final_accuracy": self.final_accuracy,
            "final_rmse": self.final_rmse,
            "final_r2": self.final_r2,
            "history": [
                {
                    "step": h.step,
                    "n_experiments": h.n_experiments,
                    "accuracy": round(h.accuracy, 4),
                    "f1": round(h.f1, 4),
                    "rmse": round(h.rmse, 2),
                    "r2": round(h.r2, 4),
                }
                for h in self.history
            ],
        }


@dataclass
class BenchmarkSummary:
    target: str
    threshold: float
    target_accuracy: float
    n_init: int
    n_steps: int
    results: dict[str, StrategyResult] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "threshold": self.threshold,
            "target_accuracy": self.target_accuracy,
            "n_init": self.n_init,
            "n_steps": self.n_steps,
            "results": {k: v.to_dict() for k, v in self.results.items()},
        }


def _scale_features_minmax(
    X_train: np.ndarray, X_test: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Min-max scale features to [0, 1] based on X_train bounds."""
    min_vals = X_train.min(axis=0)
    max_vals = X_train.max(axis=0)
    diff = np.where(max_vals - min_vals < 1e-8, 1.0, max_vals - min_vals)
    X_train_scaled = (X_train - min_vals) / diff
    X_test_scaled = (X_test - min_vals) / diff
    return X_train_scaled, X_test_scaled


class ActiveLearningBenchmark:
    """Benchmark comparing BO, Random, and Grid search for SLO boundary discovery.

    Args:
        df_pool: Unlabelled candidate pool DataFrame (training split).
        df_heldout: Test set DataFrame (held-out split) for unbiased evaluation.
        target: Target column to model (default: ``"p95_latency_ms"``).
        threshold: SLO boundary threshold (default: 500.0 ms).
        target_accuracy: Accuracy threshold for milestone tracking (default: 0.80).
        n_init: Number of initial randomly seeded experiments (default: 8).
        n_steps: Number of sequential acquisition rounds (default: 40).
        beta: Straddle acquisition trade-off parameter (default: 1.96).
        acq_method: Acquisition formula (``"straddle"`` or ``"weighted_uncertainty"``).
        seed: Random seed for reproducibility.
    """

    def __init__(
        self,
        df_pool: pd.DataFrame,
        df_heldout: pd.DataFrame,
        target: str = "p95_latency_ms",
        threshold: float = 500.0,
        target_accuracy: float = 0.80,
        n_init: int = 8,
        n_steps: int = 40,
        beta: float = 1.96,
        acq_method: AcquisitionMethod = "straddle",
        seed: int = 42,
    ) -> None:
        self.df_pool = df_pool.reset_index(drop=True)
        self.df_heldout = df_heldout.reset_index(drop=True)
        self.target = target
        self.threshold = float(threshold)
        self.target_accuracy = float(target_accuracy)
        self.n_init = n_init
        self.n_steps = n_steps
        self.beta = beta
        self.acq_method: AcquisitionMethod = acq_method
        self.seed = seed

        # Build features and min-max scale to [0, 1] for BoTorch models
        X_p, scaler = build_feature_matrix(self.df_pool)
        X_h, _ = build_feature_matrix(self.df_heldout, scaler=scaler)
        self.X_pool_np, self.X_heldout_np = _scale_features_minmax(X_p, X_h)

        self.y_pool_np = self.df_pool[self.target].to_numpy(dtype=float)
        self.y_heldout_np = self.df_heldout[self.target].to_numpy(dtype=float)

        # Binary SLO ground truth (1 = compliant <= threshold, 0 = violation > threshold)
        self.slo_true = (self.y_heldout_np <= self.threshold).astype(int)

        self.X_pool = torch.tensor(self.X_pool_np, dtype=torch.float64)
        self.y_pool = torch.tensor(self.y_pool_np, dtype=torch.float64).unsqueeze(-1)
        self.X_heldout = torch.tensor(self.X_heldout_np, dtype=torch.float64)

    def _fit_model(self, selected_indices: list[int]) -> SingleTaskGP:
        """Fit a SingleTaskGP on the selected experiments."""
        train_x = self.X_pool[selected_indices]
        train_y = self.y_pool[selected_indices]
        model = SingleTaskGP(train_x, train_y, outcome_transform=Standardize(m=1))
        mll = ExactMarginalLogLikelihood(model.likelihood, model)
        try:
            fit_gpytorch_mll(mll)
        except Exception as exc:
            logger.debug("fit_gpytorch_mll warning/fallback at n=%d: %s", len(selected_indices), exc)
        return model

    def _evaluate_model(self, model: SingleTaskGP) -> tuple[float, float, float, float]:
        """Compute held-out accuracy, f1, rmse, and r2."""
        with torch.no_grad():
            post = model.posterior(self.X_heldout)
            pred_mean = post.mean.squeeze(-1).cpu().numpy()

        slo_pred = (pred_mean <= self.threshold).astype(int)
        acc = float(accuracy_score(self.slo_true, slo_pred))
        f1 = float(f1_score(self.slo_true, slo_pred, zero_division=0.0))
        rmse = float(np.sqrt(np.mean((self.y_heldout_np - pred_mean) ** 2)))
        r2 = float(r2_score(self.y_heldout_np, pred_mean))
        return acc, f1, rmse, r2

    def run_strategy(
        self,
        strategy: StrategyType | str,
        init_indices: list[int],
    ) -> StrategyResult:
        """Execute sequential experiment selection for *strategy*.

        Args:
            strategy: Strategy name (``"bo"``, ``"random"``, or ``"grid"``).
            init_indices: Initial indices to seed the model with.

        Returns:
            :class:`StrategyResult` containing the history of accuracy/rmse at each step.
        """
        strat_name = str(strategy).lower()
        if strat_name not in ("bo", "random", "grid"):
            raise ValueError(f"Unknown strategy {strategy!r}")

        selected = list(init_indices)
        rng = np.random.default_rng(self.seed)

        # Precompute grid order: lexicographical traversal across primary features
        if strat_name == "grid":
            grid_order = list(
                np.lexsort((self.X_pool_np[:, 0], self.X_pool_np[:, 1], self.X_pool_np[:, 2]))
            )
            grid_ptr = 0

        history: list[StepRecord] = []
        experiments_to_target: int | None = None

        logger.info("Running strategy: %s (%d steps, n_init=%d)", strat_name, self.n_steps, len(selected))

        for step in range(self.n_steps):
            n_exp = len(selected)
            model = self._fit_model(selected)
            acc, f1, rmse, r2 = self._evaluate_model(model)

            last_added = selected[-1]
            history.append(
                StepRecord(
                    step=step,
                    n_experiments=n_exp,
                    accuracy=acc,
                    f1=f1,
                    rmse=rmse,
                    r2=r2,
                    selected_idx=last_added,
                )
            )

            # Track milestone: first time achieving >= target_accuracy
            if experiments_to_target is None and acc >= self.target_accuracy:
                experiments_to_target = n_exp

            remaining = [i for i in range(len(self.df_pool)) if i not in selected]
            if not remaining or step == self.n_steps - 1:
                break

            # Selection step
            if strat_name == "bo":
                best_idx = select_next_candidate_bo(
                    model=model,
                    X_pool=self.X_pool,
                    remaining_indices=remaining,
                    threshold=self.threshold,
                    beta=self.beta,
                    method=self.acq_method,
                )
            elif strat_name == "random":
                best_idx = int(rng.choice(remaining))
            elif strat_name == "grid":
                while grid_ptr < len(grid_order) and grid_order[grid_ptr] in selected:
                    grid_ptr += 1
                if grid_ptr < len(grid_order):
                    best_idx = grid_order[grid_ptr]
                else:
                    best_idx = int(rng.choice(remaining))

            selected.append(best_idx)

        final_rec = history[-1]
        return StrategyResult(
            strategy=strat_name,
            history=history,
            experiments_to_target=experiments_to_target,
            final_accuracy=final_rec.accuracy,
            final_rmse=final_rec.rmse,
            final_r2=final_rec.r2,
        )

    def run_all(self) -> BenchmarkSummary:
        """Run BO, Random, and Grid search using a common initial seed set."""
        rng = np.random.default_rng(self.seed)
        init_indices = list(rng.choice(len(self.df_pool), size=self.n_init, replace=False))

        results: dict[str, StrategyResult] = {}
        for strat in (StrategyType.BO, StrategyType.RANDOM, StrategyType.GRID):
            res = self.run_strategy(strat, init_indices=init_indices)
            results[strat.value] = res
            target_str = str(res.experiments_to_target) if res.experiments_to_target else f">{res.history[-1].n_experiments}"
            logger.info(
                "Strategy %-7s -> Target acc (>=%.0f%%) in %s experiments (Final Acc=%.1f%%, Final RMSE=%.1f)",
                strat.value.upper(),
                self.target_accuracy * 100,
                target_str,
                res.final_accuracy * 100,
                res.final_rmse,
            )

        return BenchmarkSummary(
            target=self.target,
            threshold=self.threshold,
            target_accuracy=self.target_accuracy,
            n_init=self.n_init,
            n_steps=self.n_steps,
            results=results,
        )
