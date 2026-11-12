"""Gaussian Process regression for one system output (default: p95 latency).

f(x) ~ GP(m(x), k(x, x'))   with x = (lambda, r, a, K, B)

* Inputs are scaled to the unit cube using the search-space bounds.
* Latency is modelled in log space: it is positive, spans orders of
  magnitude and its noise grows with its level, so log makes the GP's
  homoscedastic-noise assumption much more reasonable.
* Kernel: constant * Matern-5/2 with one length-scale per input (ARD) +
  white noise (repeated experiments give different measurements).

One GP per output; throughput / error-rate models are separate instances.
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np
from scipy.stats import norm
from sklearn.exceptions import ConvergenceWarning
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import ConstantKernel, Matern, WhiteKernel

from experiment_engine.experiment_config import DEFAULT_SPACE, SearchSpace


@dataclass
class Prediction:
    mean: np.ndarray        # predicted output in original units (median if log)
    std_model: np.ndarray   # predictive std in model space (log units if log)
    mu_model: np.ndarray    # predictive mean in model space


class OutputGP:
    def __init__(self, space: SearchSpace = DEFAULT_SPACE, log_target: bool = True,
                 floor: float = 1.0, n_restarts: int = 3, seed: int = 0):
        self.space = space
        self.log_target = log_target
        self.floor = floor
        d = space.dim
        kernel = (ConstantKernel(1.0, (1e-2, 1e2))
                  * Matern(length_scale=np.full(d, 0.5), length_scale_bounds=(1e-2, 1e2), nu=2.5)
                  + WhiteKernel(1e-2, (1e-6, 1e0)))
        self.model = GaussianProcessRegressor(kernel=kernel, normalize_y=True,
                                              n_restarts_optimizer=n_restarts,
                                              random_state=seed)
        self.n_train = 0

    # -------------------------------------------------------------- helpers
    def _t(self, y: np.ndarray) -> np.ndarray:
        y = np.maximum(np.asarray(y, float), self.floor)
        return np.log(y) if self.log_target else y

    def _inv(self, z: np.ndarray) -> np.ndarray:
        return np.exp(z) if self.log_target else z

    # ------------------------------------------------------------------ API
    def fit(self, X: np.ndarray, y: np.ndarray) -> "OutputGP":
        with warnings.catch_warnings():
            # length-scales hitting the bound just mean "input barely matters"
            warnings.simplefilter("ignore", ConvergenceWarning)
            self.model.fit(self.space.to_unit(X), self._t(y))
        self.n_train = len(y)
        return self

    def predict(self, X: np.ndarray) -> Prediction:
        mu, sd = self.model.predict(self.space.to_unit(X), return_std=True)
        return Prediction(self._inv(mu), sd, mu)

    def prob_exceeds(self, X: np.ndarray, threshold: float) -> np.ndarray:
        """P(output > threshold) under the GP posterior."""
        p = self.predict(X)
        z = (self._t(np.array([threshold]))[0] - p.mu_model) / np.maximum(p.std_model, 1e-9)
        return 1.0 - norm.cdf(z)

    def interval(self, X: np.ndarray, beta: float = 1.96) -> Tuple[np.ndarray, np.ndarray]:
        p = self.predict(X)
        return self._inv(p.mu_model - beta * p.std_model), self._inv(p.mu_model + beta * p.std_model)

    def length_scales(self) -> dict:
        """Learned ARD length-scales: short = output is sensitive to that input."""
        k = self.model.kernel_
        ls = k.k1.k2.length_scale
        return dict(zip(self.space.names, np.atleast_1d(ls).round(3).tolist()))

    def noise_level(self) -> float:
        return float(self.model.kernel_.k2.noise_level)
