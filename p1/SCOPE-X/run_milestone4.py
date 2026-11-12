"""Milestone 4: constraint synthesis + Z3 checks + CEGIS refinement.

Needs the Milestone 1-3 outputs (run ``python run_pipeline.py`` first):
  datasets/bo_trace.csv     BO-selected experiments (starting evidence)
  datasets/validation.csv   held-out experiments (ground truth for scoring)

Outputs
  constraint_repository/constraints.json (+ evidence/)
  datasets/cegis_trace.csv, datasets/cegis_comparison.csv
  results/milestone4_summary.json, results/figures/08_constraint_refinement.png

    python run_milestone4.py            # ~2 min
    python run_milestone4.py --quick
"""
from __future__ import annotations

import argparse
import json
import os
import time

import numpy as np
import pandas as pd

from constraint_repository.repository import ConstraintRepository
from evaluation import plots
from evaluation.constraint_metrics import refinement_report, rule_report
from experiment_engine.data_collector import DataCollector
from experiment_engine.experiment_config import DEFAULT_SLO, DEFAULT_SPACE
from experiment_engine.experiment_runner import run_experiment
from or_engine.cegis import CEGIS
from or_engine.constraint_synthesis import synthesize_cv, synthesize_monotone
from or_engine.rules import RAW
from or_engine.z3_verification import HAVE_Z3, Verifier

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "datasets")
FIGS = os.path.join(ROOT, "results", "figures")
S, SLO = DEFAULT_SPACE, DEFAULT_SLO
L = SLO.L95_max_ms


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def sim(x, seed):
    return run_experiment(x, seed=seed)


def measure(X, seed0):
    return np.array([sim(S.as_dict(x), seed0 + i)["L95_ms"] for i, x in enumerate(X)])


def refinement_vs_random(ref_X, ref_y, reps, n0, iters, tests, synth="monotone"):
    """Weak start (n0 random experiments) -> CEGIS vs the same number of
    extra *random* experiments, scored on the held-out set after each
    CEGIS iteration."""
    synth_fn = synthesize_monotone if synth == "monotone" else synthesize_cv
    ev = lambda r: rule_report(r, ref_X, ref_y, L)
    rows, cex_rows = [], []
    for rep in range(reps):
        X0 = S.sample(n0, np.random.default_rng(300 + rep))
        y0 = measure(X0, 80_000 + rep * 100)
        c = CEGIS(sim, S, SLO, seed=rep, max_tests_per_iter=tests, synthesizer=synth, evaluate=ev)
        c.run(X0, y0, max_iters=iters)
        init_f1 = c.initial_metrics["f1"]
        n_cum = np.cumsum([h.tested for h in c.history])
        Xr = S.sample(int(n_cum[-1]) if len(n_cum) else 0, np.random.default_rng(900 + rep))
        yr = measure(Xr, 90_000 + rep * 100)
        rows.append({"rep": rep, "n_extra": 0, "strategy": "cegis", "f1": init_f1})
        rows.append({"rep": rep, "n_extra": 0, "strategy": "random", "f1": init_f1})
        for h, n in zip(c.history, n_cum):
            rows.append({"rep": rep, "n_extra": int(n), "strategy": "cegis", "f1": h.metrics["f1"]})
            rr = synth_fn(np.vstack([X0, Xr[:n]]), np.concatenate([y0, yr[:n]]) > L)
            rows.append({"rep": rep, "n_extra": int(n), "strategy": "random", "f1": ev(rr)["f1"]})
            for src, (t, k) in h.by_source.items():
                cex_rows.append({"rep": rep, "iteration": h.it, "source": src, "tested": t,
                                 "counterexamples": k})
        log(f"  [{synth}] rep {rep}: F1 {init_f1:.3f} -> CEGIS {rows[-2]['f1']:.3f} | "
            f"random {rows[-1]['f1']:.3f}  ({int(n_cum[-1])} extra experiments)")
    return pd.DataFrame(rows), pd.DataFrame(cex_rows)


def main(quick=False):
    for f in ("bo_trace.csv", "validation.csv"):
        if not os.path.exists(os.path.join(DATA, f)):
            raise SystemExit(f"datasets/{f} missing - run `python run_pipeline.py` first")
    ref = pd.read_csv(os.path.join(DATA, "validation.csv"))
    bo = pd.read_csv(os.path.join(DATA, "bo_trace.csv"))
    ref_X, ref_y = ref[RAW].values, ref["L95_ms"].values
    backend = "z3" if HAVE_Z3 else "exact"
    summary = {"verifier_backend": backend, "slo_L95_max_ms": L}
    reps, iters, tests = (3, 4, 8) if quick else (6, 5, 8)

    # -------------------------------------------- A. refinement experiment
    log(f"A. Refinement vs random extra experiments ({reps} repeats, start = 15 random)")
    cmp_df, cex_df = refinement_vs_random(ref_X, ref_y, reps, 15, iters, tests, "monotone")
    log("   ablation: same loop with the decision-tree synthesiser")
    cmp_tree, _ = refinement_vs_random(ref_X, ref_y, reps, 15, iters, tests, "tree")
    cmp_df.assign(synthesizer="monotone").pipe(
        lambda d: pd.concat([d, cmp_tree.assign(synthesizer="tree")])).to_csv(
        os.path.join(DATA, "cegis_comparison.csv"), index=False)
    cex_df.to_csv(os.path.join(DATA, "cegis_counterexamples.csv"), index=False)
    plots.fig_constraint_refinement(cmp_df, cex_df, FIGS)

    def final(df):
        last = df.groupby(["rep", "strategy"])["n_extra"].transform("max") == df["n_extra"]
        g = df[last].groupby("strategy")["f1"]
        return {s: {"mean_f1": round(float(v.mean()), 4), "min": round(float(v.min()), 4),
                    "max": round(float(v.max()), 4)} for s, v in g}

    init = cmp_df[cmp_df.n_extra == 0].groupby("rep")["f1"].first()
    summary["refinement_experiment"] = {
        "start": "15 random experiments", "repeats": reps, "max_iterations": iters,
        "tests_per_iteration": tests,
        "before_refinement_mean_f1": round(float(init.mean()), 4),
        "after_monotone": final(cmp_df), "after_tree_ablation": final(cmp_tree),
        "mean_extra_experiments": float(cmp_df.groupby("rep")["n_extra"].max().mean()),
        "counterexample_hit_rate_by_source": (cex_df.groupby("source")[["tested", "counterexamples"]]
                                              .sum().assign(rate=lambda d: d.counterexamples / d.tested)
                                              .round(3).to_dict(orient="index")),
    }

    # ------------------------------ B. refine the BO-derived constraint
    log("B. CEGIS on the Milestone 3 BO evidence -> constraint repository")
    ev = lambda r: rule_report(r, ref_X, ref_y, L)
    c = CEGIS(sim, S, SLO, seed=42, max_tests_per_iter=tests, evaluate=ev)
    rule = c.run(bo[RAW].values, bo["L95_ms"].values, max_iters=iters, log=log)
    checks = Verifier(S, seed=1).check_all(rule)
    rep = refinement_report(c)
    m_final = ev(rule)
    DataCollector(os.path.join(DATA, "cegis_trace.csv")).reset()
    DataCollector(os.path.join(DATA, "cegis_trace.csv")).extend(c.rows)
    validated = m_final["f1"] >= 0.85 and rep["unresolved_validation_failures"] <= 0.05 * len(c.y)
    entry = ConstraintRepository().add(
        "C1_checkout_p95_latency", rule,
        slo={"L95_max_ms": L},
        domain={v.name: [v.low, v.high] for v in S.variables},
        scale={"replicas": [S.variables[1].low, S.variables[1].high], "db_pool_size": 8,
               "workers_per_replica": 4, "testbed": "simulated (Milestones 1-3 surrogate)"},
        evidence_rows=bo.to_dict(orient="records") + c.rows,
        status="validated" if validated else "candidate",
        verification=[x.to_dict() for x in checks],
        metrics={"held_out": {k: round(v, 4) if isinstance(v, float) else v for k, v in m_final.items()},
                 "before_refinement": {k: round(v, 4) if isinstance(v, float) else v
                                       for k, v in c.initial_metrics.items()}},
        refinement=rep,
        notes="Validation criterion: held-out F1 >= 0.85 and unresolved failures <= 5% of evidence. "
              "Formal results hold only within the encoded domain and assumptions."
              + (" Formal checks were executed by the exact fallback solver (backend 'exact'); the Z3 encoding is "
                 "implemented but not yet executed or cross-validated." if not HAVE_Z3 else "")
              + " Mechanism explanations (e.g. shared-DB saturation for clause 3) are hypotheses, not established by the rule.")
    summary["constraint_C1"] = {
        "initial_rule": c.initial_rule.text(), "final_rule": rule.text(),
        "status": entry["status"], "held_out_before": entry["metrics"]["before_refinement"],
        "held_out_after": entry["metrics"]["held_out"], "refinement": rep,
        "final_checks": [{k: v for k, v in x.to_dict().items() if k != "witness"} for x in checks],
    }

    with open(os.path.join(ROOT, "results", "milestone4_summary.json"), "w") as f:
        json.dump(summary, f, indent=2, default=float)
    log("Done -> results/milestone4_summary.json, constraint_repository/constraints.json, "
        "results/figures/08_constraint_refinement.png")
    print(json.dumps(summary["refinement_experiment"], indent=1, default=float))
    print(json.dumps({k: summary["constraint_C1"][k] for k in
                      ("initial_rule", "final_rule", "status", "held_out_before", "held_out_after")},
                     indent=1, default=float))
    print(json.dumps(summary["constraint_C1"]["refinement"], indent=1, default=float))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    main(ap.parse_args().quick)
