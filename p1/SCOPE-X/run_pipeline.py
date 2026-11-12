"""SCOPE-X first target, end to end (Milestones 1-3).

  1. Flash-sale Shield demo         -> graphs 1-4
  2. Initial experimental dataset   -> datasets/experiments.csv
  3. Held-out validation set        -> datasets/validation.csv
  4. GP on p95 latency              -> RMSE/MAE, graphs 5-6
  5. Safe BO loop                   -> datasets/bo_trace.csv
  6. BO vs random vs grid           -> datasets/comparison.csv, graph 7
  7. Summary                        -> results/summary.json

    python run_pipeline.py            # full run (~2-4 min on a laptop)
    python run_pipeline.py --quick    # smaller budgets for a smoke test
"""
from __future__ import annotations

import argparse
import json
import os
import time

import numpy as np
import pandas as pd

from experiment_engine.data_collector import DataCollector
from experiment_engine.experiment_config import (DEFAULT_SAFETY, DEFAULT_SLO, DEFAULT_SPACE,
                                                 SafetyLimits)
from experiment_engine.experiment_runner import run_design, run_experiment
from evaluation import plots
from evaluation.comparison import compare, experiments_to_target, score
from or_engine.bayesian_optimization import SafeActiveLearner
from or_engine.gaussian_process import OutputGP

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "datasets")
FIGS = os.path.join(ROOT, "results", "figures")

# Shield switched off: unlimited tokens, no waiting room, no admission caps.
NO_SHIELD = dict(admission_rate=1e6, bucket_capacity=10 ** 6, queue_capacity=0,
                 max_inflight=10 ** 6, downstream_limit=10 ** 6)
NO_STOP = SafetyLimits(L_stop_ms=1e12, U_stop=2.0)   # demo only: show the collapse


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def sim(x, seed):
    return run_experiment(x, seed=seed)


def main(quick: bool = False):
    os.makedirs(DATA, exist_ok=True)
    os.makedirs(FIGS, exist_ok=True)
    S, slo = DEFAULT_SPACE, DEFAULT_SLO
    summary = {"slo": slo.__dict__, "safety": DEFAULT_SAFETY.__dict__, "backend": "sim"}

    # ---------------------------------------------------------------- 1. shield demo
    log("1/7 Flash-sale Shield demo")
    demo = dict(traffic_rate=600, replicas=2, admission_rate=200, queue_capacity=500, bucket_capacity=100)
    row_s, ts_s = run_experiment(demo, seed=11, return_timeseries=True)
    row_n, ts_n = run_experiment(demo, seed=11, return_timeseries=True,
                                 shield_override=NO_SHIELD, safety=NO_STOP)
    plots.fig_traffic_vs_admitted(ts_s, ts_n, FIGS, "λ=600, a=200, r=2")
    plots.fig_queue_vs_time(ts_s, demo["queue_capacity"], FIGS)
    keep = ["L95_ms", "throughput_rps", "error_rate", "rejection_rate", "Qw95_ms", "queue_max"]
    summary["demo"] = {"config": demo, "with_shield": {k: row_s[k] for k in keep},
                       "without_shield": {k: row_n[k] for k in keep}}

    sweep = []
    for r in (2, 4):
        for a in np.linspace(100, 500, 9):
            sweep.append(run_experiment(dict(demo, traffic_rate=500, replicas=r, admission_rate=a), seed=int(a) + r))
    plots.fig_wait_vs_admission(pd.DataFrame(sweep), FIGS, 500)

    lat = []
    for r in (2, 4):
        for lam in np.linspace(100, 800, 8):
            x = dict(demo, traffic_rate=lam, replicas=r, admission_rate=110 * r)  # ~ capacity
            lat.append({**run_experiment(x, seed=int(lam)), "mode": "shield"})
            lat.append({**run_experiment(x, seed=int(lam), shield_override=NO_SHIELD, safety=NO_STOP),
                        "mode": "no shield"})
    plots.fig_latency_vs_traffic(pd.DataFrame(lat), slo.L95_max_ms, FIGS)

    # ------------------------------------------------------- 2-3. datasets
    n_train, n_ref = (25, 120) if quick else (40, 300)
    log(f"2/7 Initial dataset: {n_train} random experiments")
    train_c = DataCollector(os.path.join(DATA, "experiments.csv")); train_c.reset()
    rows = run_design(S.sample(n_train, np.random.default_rng(1)), S, 10_000, "random_init",
                      collector=train_c)
    log(f"3/7 Validation (reference) set: {n_ref} experiments")
    ref_c = DataCollector(os.path.join(DATA, "validation.csv")); ref_c.reset()
    ref_rows = run_design(S.sample(n_ref, np.random.default_rng(99)), S, 900_000, "reference",
                          collector=ref_c)
    ref_X = np.array([[r[n] for n in S.names] for r in ref_rows])
    ref_y = np.array([r["L95_ms"] for r in ref_rows])
    summary["reference_violation_share"] = float((ref_y > slo.L95_max_ms).mean())

    # ------------------------------------------------------------- 4. GP
    log("4/7 GP on p95 latency")
    X = np.array([[r[n] for n in S.names] for r in rows])
    y = np.array([r["L95_ms"] for r in rows])
    gp = OutputGP(S).fit(X, y)
    sc = score(gp, ref_X, ref_y, slo)
    lo, hi = gp.interval(ref_X)
    pred = gp.predict(ref_X).mean
    plots.fig_gp_pred_vs_actual(ref_y, pred, lo, hi, slo.L95_max_ms, sc, FIGS)
    inside = float(np.mean((ref_y >= lo) & (ref_y <= hi)))
    summary["gp_random_init"] = {"n_train": n_train, **sc, "interval95_coverage": inside,
                                 "length_scales": gp.length_scales(), "noise": gp.noise_level()}

    # ------------------------------------------------------------- 5. BO
    budget = 30 if quick else 60
    log(f"5/7 Safe BO loop (budget {budget})")
    learner = SafeActiveLearner(sim, S, slo, acq="straddle", seed=7)
    learner.run(budget, n_init=8)
    bo_c = DataCollector(os.path.join(DATA, "bo_trace.csv")); bo_c.reset(); bo_c.extend(learner.state.rows)
    sc_bo = score(learner.gp, ref_X, ref_y, slo)
    summary["gp_after_bo"] = {"n_train": budget, **sc_bo, "length_scales": learner.gp.length_scales(),
                              "unsafe_candidates_screened_last": learner.state.unsafe_screened[-1],
                              "aborted_experiments": int(sum(r["aborted"] for r in learner.state.rows))}

    # graph 6 on the BO model: slice over (lambda, a) at r=2, K=500, B=100
    lam_g = np.linspace(S.variables[0].low, S.variables[0].high, 60)
    a_g = np.linspace(S.variables[2].low, S.variables[2].high, 60)
    LL, AA = np.meshgrid(lam_g, a_g)
    G = np.column_stack([LL.ravel(), np.full(LL.size, 2), AA.ravel(), np.full(LL.size, 500), np.full(LL.size, 100)])
    p = learner.gp.predict(G)
    Xbo = np.array(learner.state.X)
    plots.fig_gp_uncertainty(lam_g, a_g, p.mean.reshape(LL.shape), p.std_model.reshape(LL.shape),
                             Xbo[:, [0, 2]], slo.L95_max_ms, "slice at r = 2, K = 500, B = 100", FIGS)

    # ------------------------------------------------------- 6. comparison
    reps = 2 if quick else 3
    checkpoints = (8, 12, 16, 20, 25, 30) if quick else (8, 12, 16, 20, 25, 30, 40, 50, 60)
    log(f"6/7 BO vs random vs grid ({reps} repeats, budget {budget})")
    cmp_df = compare(sim, ref_X, ref_y, budget=budget, reps=reps, checkpoints=checkpoints,
                     log=log)
    cmp_df.to_csv(os.path.join(DATA, "comparison.csv"), index=False)
    plots.fig_bo_vs_baselines(cmp_df, FIGS)
    final = cmp_df[cmp_df["n"] == budget].groupby("strategy")[["f1", "precision", "recall", "accuracy",
                                                                "rmse_log", "mae_log"]].mean()
    summary["comparison_at_budget"] = final.round(4).to_dict(orient="index")
    for t in (0.85, 0.9):
        summary[f"experiments_to_f1_{t}"] = experiments_to_target(cmp_df, "f1", t)

    # ------------------------------------------------------------- 7. summary
    with open(os.path.join(ROOT, "results", "summary.json"), "w") as f:
        json.dump(summary, f, indent=2, default=float)
    log("7/7 Done -> results/summary.json, results/figures/, datasets/")
    print(json.dumps({k: summary[k] for k in ("demo", "gp_random_init", "gp_after_bo",
                                             "comparison_at_budget", "experiments_to_f1_0.85",
                                             "experiments_to_f1_0.9")}, indent=1, default=float))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    main(ap.parse_args().quick)
