"""GP surrogate model for predicting p95 latency and error rate from configuration.

Uses GPyTorch ExactGP with a Matérn-5/2 ARD kernel. Each target (p95_latency_ms,
error_rate_pct) is modelled by an independent surrogate.
"""

from __future__ import annotations

import logging
import math
from pathlib import Path

import gpytorch
import numpy as np
import pandas as pd
import torch
from sklearn.preprocessing import StandardScaler

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROFILE_ENCODER: dict[str, int] = {
    "normal": 0,
    "flash_sale": 1,
    "ramp": 2,
    "viral_product": 3,
    "slow_payment_api": 4,
}

CONTINUOUS_FEATURES: list[str] = [
    "target_rps",
    "checkout_replicas",
    "db_pool_size",
    "consumers",
]

SUPPORTED_TARGETS: list[str] = ["p95_latency_ms", "error_rate_pct"]


# ---------------------------------------------------------------------------
# Feature engineering
# ---------------------------------------------------------------------------


def build_feature_matrix(df: pd.DataFrame, scaler: StandardScaler | None = None) -> tuple[np.ndarray, StandardScaler]:
    """Extract and normalise input features from a configuration DataFrame.

    Features (in order):
        0 – target_rps          (continuous, standardised)
        1 – checkout_replicas   (continuous, standardised)
        2 – db_pool_size        (continuous, standardised)
        3 – consumers           (continuous, standardised)
        4 – cache_enabled       (binary 0/1, kept as-is)
        5 – workload_profile    (ordinal-encoded, standardised with continuous)

    Args:
        df: Input DataFrame (must contain the feature columns).
        scaler: Pre-fitted StandardScaler. When *None* a new scaler is fitted on *df*.

    Returns:
        (X, scaler) where X is shape (n, 6) float64 and scaler can be reused.
    """
    profile_idx = df["workload_profile"].map(PROFILE_ENCODER).fillna(0).astype(float)

    continuous = df[CONTINUOUS_FEATURES].copy().astype(float)
    continuous["profile_idx"] = profile_idx.values

    if scaler is None:
        scaler = StandardScaler()
        cont_scaled = scaler.fit_transform(continuous.values)
    else:
        cont_scaled = scaler.transform(continuous.values)

    cache = df["cache_enabled"].astype(float).values.reshape(-1, 1)
    X = np.hstack([cont_scaled, cache])  # shape (n, 6)
    return X, scaler


# ---------------------------------------------------------------------------
# GPyTorch model
# ---------------------------------------------------------------------------


class _ExactGPModel(gpytorch.models.ExactGP):
    """Single-output ExactGP with Matérn-5/2 ARD kernel + constant mean."""

    def __init__(
        self,
        train_x: torch.Tensor,
        train_y: torch.Tensor,
        likelihood: gpytorch.likelihoods.GaussianLikelihood,
        n_features: int,
    ) -> None:
        super().__init__(train_x, train_y, likelihood)
        self.mean_module = gpytorch.means.ConstantMean()
        self.covar_module = gpytorch.kernels.ScaleKernel(
            gpytorch.kernels.MaternKernel(nu=2.5, ard_num_dims=n_features)
        )

    def forward(self, x: torch.Tensor) -> gpytorch.distributions.MultivariateNormal:  # type: ignore[override]
        mean_x = self.mean_module(x)
        covar_x = self.covar_module(x)
        return gpytorch.distributions.MultivariateNormal(mean_x, covar_x)


# ---------------------------------------------------------------------------
# Public surrogate wrapper
# ---------------------------------------------------------------------------


class GPSurrogate:
    """Train and query a GP surrogate for a single scalar target.

    Args:
        target: Column name in the experiment DataFrame to predict.
        n_iter: Number of Adam optimisation steps.
        lr: Learning rate for Adam.
        device: ``"cpu"`` or ``"cuda"`` (falls back to cpu if unavailable).

    Example::

        import pandas as pd
        df_train = pd.read_parquet("data/experiments.parquet").query("split == 'train'")
        sur = GPSurrogate(target="p95_latency_ms").fit(df_train)
        df_test = pd.read_parquet("data/experiments.parquet").query("split == 'held_out'")
        mean, std = sur.predict(df_test)
    """

    def __init__(
        self,
        target: str = "p95_latency_ms",
        n_iter: int = 200,
        lr: float = 0.1,
        device: str = "cpu",
    ) -> None:
        if target not in SUPPORTED_TARGETS:
            raise ValueError(f"target must be one of {SUPPORTED_TARGETS}, got {target!r}")
        self.target = target
        self.n_iter = n_iter
        self.lr = lr
        self.device = torch.device(device)

        # Populated after fit()
        self._scaler: StandardScaler | None = None
        self._y_mean: float = 0.0
        self._y_std: float = 1.0
        self._model: _ExactGPModel | None = None
        self._likelihood: gpytorch.likelihoods.GaussianLikelihood | None = None

    # ------------------------------------------------------------------
    # Fit
    # ------------------------------------------------------------------

    def fit(self, df_train: pd.DataFrame) -> GPSurrogate:
        """Fit the GP on *df_train*.

        Args:
            df_train: DataFrame with feature columns and ``self.target`` column.

        Returns:
            self (for method chaining).
        """
        X_np, scaler = build_feature_matrix(df_train)
        self._scaler = scaler

        y_np = df_train[self.target].to_numpy(dtype=float)
        # Standardise targets for numerical stability
        self._y_mean = float(y_np.mean())
        self._y_std = float(y_np.std()) if y_np.std() > 1e-8 else 1.0
        y_norm = (y_np - self._y_mean) / self._y_std

        train_x = torch.tensor(X_np, dtype=torch.float64).to(self.device)
        train_y = torch.tensor(y_norm, dtype=torch.float64).to(self.device)

        n_features = train_x.shape[1]
        likelihood = gpytorch.likelihoods.GaussianLikelihood().to(self.device).double()
        model = _ExactGPModel(train_x, train_y, likelihood, n_features).to(self.device).double()

        model.train()
        likelihood.train()

        optimizer = torch.optim.Adam(model.parameters(), lr=self.lr)
        mll = gpytorch.mlls.ExactMarginalLogLikelihood(likelihood, model)

        logger.info("Training GP for target=%s over %d iterations", self.target, self.n_iter)
        last_loss = math.inf
        for i in range(self.n_iter):
            optimizer.zero_grad()
            output = model(train_x)
            loss = -mll(output, train_y)
            loss.backward()
            optimizer.step()
            if (i + 1) % 50 == 0:
                logger.debug("  iter %d/%d  loss=%.4f", i + 1, self.n_iter, loss.item())
            last_loss = loss.item()

        logger.info("Training complete. Final MLL loss: %.4f", last_loss)

        self._model = model
        self._likelihood = likelihood
        return self

    # ------------------------------------------------------------------
    # Predict
    # ------------------------------------------------------------------

    def predict(self, df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        """Return predictive mean and std in the original (un-standardised) scale.

        Args:
            df: DataFrame with feature columns (target column not required).

        Returns:
            (mean, std) each of shape (n,).
        """
        if self._model is None or self._scaler is None:
            raise RuntimeError("Call .fit() before .predict()")

        X_np, _ = build_feature_matrix(df, scaler=self._scaler)
        test_x = torch.tensor(X_np, dtype=torch.float64).to(self.device)

        self._model.eval()
        self._likelihood.eval()

        with torch.no_grad(), gpytorch.settings.fast_pred_var():
            pred = self._likelihood(self._model(test_x))
            mean_norm = pred.mean.cpu().numpy()
            std_norm = pred.variance.sqrt().cpu().numpy()

        # Undo target standardisation
        mean = mean_norm * self._y_std + self._y_mean
        std = std_norm * self._y_std  # std scales linearly
        return mean, std

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, path: str | Path) -> None:
        """Save model state dict and scaler to *path* (a .pt file).

        Args:
            path: Destination file path (created/overwritten).
        """
        if self._model is None:
            raise RuntimeError("Nothing to save — model not trained yet.")
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        torch.save(
            {
                "target": self.target,
                "n_iter": self.n_iter,
                "lr": self.lr,
                "y_mean": self._y_mean,
                "y_std": self._y_std,
                "scaler": self._scaler,
                "model_state": self._model.state_dict(),
                "likelihood_state": self._likelihood.state_dict(),
                "train_x": self._model.train_inputs[0],
                "train_y": self._model.train_targets,
            },
            path,
        )
        logger.info("Saved GP surrogate to %s", path)

    @classmethod
    def load(cls, path: str | Path) -> GPSurrogate:
        """Load a previously saved surrogate from *path*.

        Args:
            path: File previously created by :meth:`save`.

        Returns:
            Fully initialised :class:`GPSurrogate` ready for :meth:`predict`.
        """
        path = Path(path)
        data = torch.load(path, map_location="cpu", weights_only=False)

        sur = cls(target=data["target"], n_iter=data["n_iter"], lr=data["lr"])
        sur._y_mean = data["y_mean"]
        sur._y_std = data["y_std"]
        sur._scaler = data["scaler"]

        train_x: torch.Tensor = data["train_x"]
        train_y: torch.Tensor = data["train_y"]
        n_features = train_x.shape[1]

        likelihood = gpytorch.likelihoods.GaussianLikelihood().double()
        model = _ExactGPModel(train_x, train_y, likelihood, n_features).double()
        model.load_state_dict(data["model_state"])
        likelihood.load_state_dict(data["likelihood_state"])

        sur._model = model
        sur._likelihood = likelihood
        return sur
