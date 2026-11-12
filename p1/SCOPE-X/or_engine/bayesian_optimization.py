"""Safety-aware Bayesian Optimisation / active learning for constraint discovery.

GP predicts; BO chooses what to test next.

Acquisition functions (all maximised over a candidate pool):

* ``straddle`` (default) - level-set estimation for the SLO boundary
  L95 = L_max:  a(x) = 1.96*sigma(x) - |mu(x) - log L_max|.
  High where the model is uncertain *and* close to the boundary, which is
  exactly where the safe/unsafe constraint must be pinned down.
* ``ucb`` / ``ei`` / ``pi`` - classic acquisitions that look for the
  configuration minimising the modelled output; kept for comparison and
  for the later resource-optimisation stage.
* ``variance`` - pure uncertainty sampling.

Safety: a candidate is only eligible if the GP's upper confidence bound on
L95 is below the experiment stop limit L_stop (SafeOpt-style safe set).
This is a screening rule, not a guarantee - the experiment controller's
emergency stop and the shield's bounded queues remain the real protection.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

import numpy as np
from scipy.stats import norm

from experiment_engine.experiment_config import (DEFAULT_SAFETY, DEFAULT_SLO, DEFAULT_SPACE,
                                                 SafetyLimits, SearchSpace, SLO)

from .gaussian_process import OutputGP


def acquisition(name: str, mu: np.ndarray, sd: np.ndarray, level: float,
                best: float, beta: float = 1.96, xi: float = 0.01) -> np.ndarray:
    sd = np.maximum(sd, 1e-9)
    if name == "straddle":
        return beta * sd - np.abs(mu - level)
    if name == "variance":
        return sd
    if name == "ucb":                     # minimisation -> lower confidence bound
        return -(mu - beta * sd)
    if name == "ei":
        imp = best - mu - xi
        z = imp / sd
        return imp * norm.cdf(z) + sd * norm.pdf(z)
    if name == "pi":
        return norm.cdf((best - mu - xi) / sd)
    raise ValueError(f"unknown acquisition {name!r}")


@dataclass
class BOState:
    X: List[np.ndarray] = field(default_factory=list)
    y: List[float] = field(default_factory=list)
    rows: List[dict] = field(default_factory=list)
    acq_values: List[float] = field(default_factory=list)
    unsafe_screened: List[int] = field(default_factory=list)


class SafeActiveLearner:
    """Runs the GP -> BO -> experiment loop.

    ``run_fn(x_dict, seed) -> row`` executes one experiment (any backend).
    """

    def __init__(self, run_fn: Callable[[Dict[str, float], int], dict],
                 space: SearchSpace = DEFAULT_SPACE, slo: SLO = DEFAULT_SLO,
                 safety: SafetyLimits = DEFAULT_SAFETY, target: str = "L95_ms",
                 acq: str = "straddle", beta_safe: float = 2.0, n_candidates: int = 3000,
                 seed: int = 0):
        self.run_fn, self.space, self.slo, self.safety = run_fn, space, slo, safety
        self.target, self.acq, self.beta_safe = target, acq, beta_safe
        self.n_candidates = n_candidates
        self.rng = np.random.default_rng(seed)
        self.seed = seed
        self.state = BOState()
        self.gp: Optional[OutputGP] = None
        self._exp_seed = seed * 100000

    # ----------------------------------------------------------- experiments
    def _run(self, x: np.ndarray, strategy: str) -> dict:
        xd = self.space.as_dict(x)
        self._exp_seed += 1
        row = self.run_fn(xd, self._exp_seed)
        row["strategy"] = strategy
        self.state.X.append(np.array([xd[n] for n in self.space.names]))
        self.state.y.append(row[self.target])
        self.state.rows.append(row)
        return row

    def initial_design(self, n: int, conservative: bool = True) -> None:
        """Random start; 'conservative' keeps lambda in the lower half of
        its range so the first experiments are unlikely to overload."""
        U = self.rng.random((n, self.space.dim))
        if conservative:
            j = self.space.names.index("traffic_rate")
            U[:, j] *= 0.5
        for x in self.space.from_unit(U):
            self._run(x, "bo_init")

    def fit(self) -> OutputGP:
        self.gp = OutputGP(self.space, seed=self.seed).fit(np.array(self.state.X),
                                                           np.array(self.state.y))
        return self.gp

    def propose(self) -> np.ndarray:
        gp = self.fit()
        C = self.space.sample(self.n_candidates, self.rng)
        p = gp.predict(C)
        level = np.log(self.slo.L95_max_ms)
        best = float(np.min(np.log(np.maximum(self.state.y, 1.0))))
        a = acquisition(self.acq, p.mu_model, p.std_model, level, best)
        # safe set: upper confidence bound on L95 below the stop limit
        ucb = p.mu_model + self.beta_safe * p.std_model
        safe = ucb <= np.log(self.safety.L_stop_ms)
        self.state.unsafe_screened.append(int((~safe).sum()))
        if safe.any():
            a = np.where(safe, a, -np.inf)
            i = int(np.argmax(a))
        else:                          # nothing provably safe: most conservative
            i = int(np.argmin(ucb))
        self.state.acq_values.append(float(a[i]))
        return C[i]

    def step(self) -> dict:
        return self._run(self.propose(), f"bo_{self.acq}")

    def run(self, budget: int, n_init: int = 8,
            callback: Optional[Callable[["SafeActiveLearner"], None]] = None) -> BOState:
        self.initial_design(min(n_init, budget))
        if callback:
            callback(self)
        while len(self.state.y) < budget:
            self.step()
            if callback:
                callback(self)
        self.fit()
        return self.state


# ---------------------------------------------------------------- baselines
def random_design(space: SearchSpace, n: int, rng: np.random.Generator) -> np.ndarray:
    return space.sample(n, rng)


def budgeted_grid(space: SearchSpace, n: int, rng: np.random.Generator) -> np.ndarray:
    """Grid search under a budget: 2-level full factorial (corners) first,
    then the remaining points of the 3-level factorial in shuffled order."""
    lo = np.array([v.low for v in space.variables])
    hi = np.array([v.high for v in space.variables])
    mid = (lo + hi) / 2

    def factorial(levels):
        mesh = np.meshgrid(*levels, indexing="ij")
        return np.stack([m.ravel() for m in mesh], axis=1)

    g2 = factorial([[l, h] for l, h in zip(lo, hi)])
    g3 = factorial([[l, m, h] for l, m, h in zip(lo, mid, hi)])
    seen = {tuple(r) for r in g2}
    rest = np.array([r for r in g3 if tuple(r) not in seen])
    rng.shuffle(g2)
    rng.shuffle(rest)
    X = np.vstack([g2, rest])[:n]
    return space.from_unit(space.to_unit(X))
