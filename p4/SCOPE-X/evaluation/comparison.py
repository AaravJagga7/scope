"""BO vs random search vs grid search, at equal experiment budgets.

Every strategy feeds its experiments into the same GP; only the choice of
*which* experiments to run differs. Each GP is scored on a held-out
reference set (simulated ground truth) for:
  * classification of SLO violation (L95 > L_max): precision/recall/F1
  * regression of p95 latency: RMSE/MAE (log space)
"""
from __future__ import annotations

from typing import Callable, Dict, List, Sequence

import numpy as np
import pandas as pd

from experiment_engine.experiment_config import DEFAULT_SLO, DEFAULT_SPACE, SearchSpace, SLO
from or_engine.bayesian_optimization import SafeActiveLearner, budgeted_grid, random_design
from or_engine.gaussian_process import OutputGP

from .prediction_metrics import classification_report, regression_report


def score(gp: OutputGP, ref_X: np.ndarray, ref_y: np.ndarray, slo: SLO) -> Dict[str, float]:
    p_viol = gp.prob_exceeds(ref_X, slo.L95_max_ms)
    pred = gp.predict(ref_X).mean
    out = classification_report(ref_y > slo.L95_max_ms, p_viol > 0.5)
    out.update(regression_report(ref_y, pred))
    return out


def run_strategy(name: str, run_fn: Callable, budget: int, checkpoints: Sequence[int],
                 ref_X: np.ndarray, ref_y: np.ndarray, rep: int,
                 space: SearchSpace = DEFAULT_SPACE, slo: SLO = DEFAULT_SLO,
                 n_init: int = 8) -> List[dict]:
    records = []
    if name.startswith("bo"):
        acq = name.split("_", 1)[1] if "_" in name else "straddle"
        learner = SafeActiveLearner(run_fn, space, slo, acq=acq, seed=1000 + rep)

        def cb(l: SafeActiveLearner):
            n = len(l.state.y)
            if n in checkpoints:
                gp = l.gp if (l.gp is not None and l.gp.n_train == n) else l.fit()
                records.append({"strategy": name, "rep": rep, "n": n, **score(gp, ref_X, ref_y, slo)})

        learner.run(budget, n_init=n_init, callback=cb)
        return records

    rng = np.random.default_rng(2000 + rep)
    X = random_design(space, budget, rng) if name == "random" else budgeted_grid(space, budget, rng)
    ys = []
    for i, x in enumerate(X):
        ys.append(run_fn(space.as_dict(x), 3_000_000 + rep * 10_000 + i)["L95_ms"])
        n = i + 1
        if n in checkpoints:
            gp = OutputGP(space, seed=rep).fit(X[:n], np.array(ys))
            records.append({"strategy": name, "rep": rep, "n": n, **score(gp, ref_X, ref_y, slo)})
    return records


def compare(run_fn: Callable, ref_X: np.ndarray, ref_y: np.ndarray,
            strategies=("bo_straddle", "random", "grid"), budget: int = 60,
            reps: int = 3, checkpoints: Sequence[int] = (8, 12, 16, 20, 25, 30, 40, 50, 60),
            log: Callable[[str], None] = print) -> pd.DataFrame:
    rows = []
    for s in strategies:
        for r in range(reps):
            rows += run_strategy(s, run_fn, budget, checkpoints, ref_X, ref_y, r)
            log(f"  {s} rep {r}: F1@{budget} = {rows[-1]['f1']:.3f}")
    return pd.DataFrame(rows)


def experiments_to_target(df: pd.DataFrame, metric: str = "f1", target: float = 0.9) -> dict:
    """Smallest checkpoint at which the mean metric (over reps) reaches target."""
    m = df.groupby(["strategy", "n"])[metric].mean().reset_index()
    out = {}
    for s, g in m.groupby("strategy"):
        hit = g[g[metric] >= target]
        out[s] = int(hit["n"].min()) if len(hit) else None   # None = not reached
    return out
