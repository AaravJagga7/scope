"""Static figures (PNG) for the report: graphs 1-7 of the work plan."""
from __future__ import annotations

import os
from typing import Dict

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import LinearSegmentedColormap

# Validated categorical palette (light): blue, orange, aqua (aqua is < 3:1
# contrast -> every chart also carries a legend, direct labels or markers).
C1, C2, C3 = "#2a78d6", "#eb6834", "#1baf7a"
INK, INK2, MUTED, GRID, BASE = "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"
SEQ = LinearSegmentedColormap.from_list(
    "blue_seq", ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"])

plt.rcParams.update({
    "figure.facecolor": "#fcfcfb", "axes.facecolor": "#fcfcfb", "savefig.facecolor": "#fcfcfb",
    "axes.edgecolor": BASE, "axes.labelcolor": INK2, "xtick.color": MUTED, "ytick.color": MUTED,
    "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6, "axes.spines.top": False,
    "axes.spines.right": False, "font.size": 10, "axes.titlesize": 11, "axes.titleweight": "bold",
    "axes.titlecolor": INK, "legend.frameon": False, "lines.linewidth": 2, "figure.dpi": 130,
})


def _save(fig, out_dir, name):
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, name)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return path


def fig_traffic_vs_admitted(ts_shield: Dict, ts_none: Dict, out_dir: str, title_cfg: str):
    fig, ax = plt.subplots(figsize=(7.5, 3.6))
    ax.plot(ts_shield["t"], ts_shield["arrived"], color=C1, label="Incoming traffic")
    ax.plot(ts_shield["t"], ts_shield["admitted"], color=C2, label="Admitted by shield")
    ax.plot(ts_shield["t"], ts_shield["completed"], color=C3, ls="--", label="Checkout completions")
    ax.set(xlabel="Time (s)", ylabel="Requests / s",
           title=f"1. Incoming vs admitted traffic during a flash sale ({title_cfg})")
    ax.legend(loc="upper right")
    return _save(fig, out_dir, "01_traffic_vs_admitted.png")


def fig_queue_vs_time(ts_shield: Dict, K: int, out_dir: str):
    fig, ax = plt.subplots(figsize=(7.5, 3.4))
    ax.plot(ts_shield["t"], ts_shield["queue_len"], color=C1, label="Waiting-room length")
    ax.axhline(K, color=MUTED, ls=":", lw=1.2)
    ax.text(ts_shield["t"][0], K, f"  capacity K = {K}", va="bottom", color=INK2, fontsize=9)
    ax.set(xlabel="Time (s)", ylabel="Requests waiting", title="2. Queue length vs time",
           ylim=(0, K * 1.15))
    return _save(fig, out_dir, "02_queue_vs_time.png")


def fig_wait_vs_admission(df: pd.DataFrame, out_dir: str, lam: float):
    fig, ax = plt.subplots(figsize=(6.5, 3.6))
    for (r, g), color, mk in zip(df.groupby("replicas"), (C1, C2, C3), ("o", "s", "^")):
        ax.plot(g["admission_rate"], g["Qw95_ms"] / 1000, marker=mk, ms=6, color=color,
                label=f"{int(r)} replicas — p95 wait")
    ax.set(xlabel="Admission rate a (req/s)", ylabel="Waiting time (s)",
           title=f"3. Queue waiting time vs admission rate (λ = {lam:.0f} req/s)")
    ax.legend()
    return _save(fig, out_dir, "03_wait_vs_admission.png")


def fig_latency_vs_traffic(df: pd.DataFrame, slo_ms: float, out_dir: str):
    fig, ax = plt.subplots(figsize=(6.8, 3.8))
    styles = {"shield": ("-", "o"), "no shield": ("--", "x")}
    colors = {2: C1, 4: C2}
    for (mode, r), g in df.groupby(["mode", "replicas"]):
        ls, mk = styles[mode]
        ax.plot(g["traffic_rate"], g["L95_ms"], ls=ls, marker=mk, ms=6, color=colors[int(r)],
                label=f"{int(r)} replicas, {mode}")
    ax.axhline(slo_ms, color=MUTED, ls=":", lw=1.2)
    ax.text(df["traffic_rate"].min(), slo_ms, f" SLO L95 ≤ {slo_ms:.0f} ms", va="bottom",
            color=INK2, fontsize=9)
    ax.set_yscale("log")
    ax.set(xlabel="Incoming traffic λ (req/s)", ylabel="p95 checkout latency (ms, log)",
           title="4. Checkout p95 latency vs incoming traffic")
    ax.legend(fontsize=8.5)
    return _save(fig, out_dir, "04_latency_vs_traffic.png")


def fig_gp_pred_vs_actual(y_true, y_pred, lo, hi, slo_ms, metrics: Dict, out_dir: str):
    fig, ax = plt.subplots(figsize=(5.2, 5.0))
    ax.errorbar(y_true, y_pred, yerr=[y_pred - lo, hi - y_pred], fmt="o", ms=3.5, color=C1,
                ecolor="#9ec5f4", elinewidth=0.8, alpha=0.9, label="Held-out configs (95% interval)")
    lim = [max(10, min(y_true.min(), y_pred.min()) * 0.8), max(y_true.max(), y_pred.max()) * 1.2]
    ax.plot(lim, lim, color=MUTED, lw=1, ls="--")
    ax.axvline(slo_ms, color=GRID, lw=1); ax.axhline(slo_ms, color=GRID, lw=1)
    ax.set(xscale="log", yscale="log", xlim=lim, ylim=lim, xlabel="Measured p95 latency (ms)",
           ylabel="GP predicted p95 latency (ms)", title="5. GP predicted vs actual latency")
    ax.text(0.03, 0.97, f"RMSE(log) {metrics['rmse_log']:.2f}\nMAE(log) {metrics['mae_log']:.2f}\n"
            f"median |err| {metrics['median_abs_pct_err']:.0f}%", transform=ax.transAxes,
            va="top", fontsize=9, color=INK2)
    ax.legend(loc="lower right", fontsize=8)
    return _save(fig, out_dir, "05_gp_pred_vs_actual.png")


def fig_gp_uncertainty(lam, a, mean, sd, X_train, slo_ms, fixed: str, out_dir: str):
    fig, axs = plt.subplots(1, 2, figsize=(10, 4.0), sharey=True)
    ext = [lam.min(), lam.max(), a.min(), a.max()]
    im0 = axs[0].imshow(np.log10(mean), origin="lower", extent=ext, aspect="auto", cmap=SEQ)
    cs = axs[0].contour(lam, a, mean, levels=[slo_ms], colors=[C2], linewidths=2)
    axs[0].clabel(cs, fmt={slo_ms: f"SLO {slo_ms:.0f} ms"}, fontsize=8)
    fig.colorbar(im0, ax=axs[0], label="log10 predicted p95 (ms)")
    axs[0].set(title="Predicted p95 latency", xlabel="Traffic λ (req/s)", ylabel="Admission rate a (req/s)")
    im1 = axs[1].imshow(sd, origin="lower", extent=ext, aspect="auto", cmap=SEQ)
    fig.colorbar(im1, ax=axs[1], label="GP std (log units)")
    if X_train is not None and len(X_train):
        axs[1].scatter(X_train[:, 0], X_train[:, 1], s=14, facecolor="#fcfcfb", edgecolor=INK,
                       lw=0.8)
    axs[1].set(title="Prediction uncertainty  (dots = experiments run, any r, K, B)",
               xlabel="Traffic λ (req/s)")
    fig.suptitle(f"6. GP uncertainty across the search space ({fixed})", fontweight="bold", color=INK)
    return _save(fig, out_dir, "06_gp_uncertainty.png")


def fig_bo_vs_baselines(df: pd.DataFrame, out_dir: str, target_f1: float = 0.9):
    names = {"bo_straddle": "Bayesian optimisation (safe, straddle)", "random": "Random search",
             "grid": "Grid search"}
    colors = {"bo_straddle": C1, "random": C2, "grid": C3}
    markers = {"bo_straddle": "o", "random": "s", "grid": "^"}
    fig, axs = plt.subplots(1, 2, figsize=(10.5, 3.9))
    for metric, ax, lab in (("f1", axs[0], "F1 — SLO-violation classification"),
                            ("rmse_log", axs[1], "RMSE of log p95 latency")):
        for s, g in df.groupby("strategy"):
            m = g.groupby("n")[metric].agg(["mean", "min", "max"]).reset_index()
            ax.plot(m["n"], m["mean"], marker=markers[s], ms=5, color=colors[s], label=names[s])
            ax.fill_between(m["n"], m["min"], m["max"], color=colors[s], alpha=0.12, lw=0)
        ax.set(xlabel="Number of experiments", ylabel=lab)
    axs[0].axhline(target_f1, color=MUTED, ls=":", lw=1.2)
    axs[0].text(df["n"].min(), target_f1, f" target F1 {target_f1}", va="bottom", fontsize=9, color=INK2)
    axs[0].legend(loc="lower right", fontsize=8.5)
    fig.suptitle("7. BO vs random vs grid search (mean, band = min–max over repeats)",
                 fontweight="bold", color=INK)
    return _save(fig, out_dir, "07_bo_vs_baselines.png")


def fig_constraint_refinement(cmp_df: pd.DataFrame, cex_df: pd.DataFrame, out_dir: str):
    """8. Constraint accuracy before/after CEGIS refinement vs random extra data."""
    fig, axs = plt.subplots(1, 2, figsize=(10.5, 3.9), gridspec_kw={"width_ratios": [1.25, 1]})
    ax = axs[0]
    names = {"cegis": "CEGIS refinement", "random": "Same number of random experiments"}
    for s, color, mk in (("cegis", C1, "o"), ("random", C2, "s")):
        g = cmp_df[cmp_df.strategy == s]
        # iterations end at slightly different n per repeat: bin by iteration index
        g = g.assign(step=g.groupby("rep").cumcount())
        m = g.groupby("step").agg(n=("n_extra", "mean"), mean=("f1", "mean"),
                                  lo=("f1", "min"), hi=("f1", "max")).reset_index()
        ax.plot(m["n"], m["mean"], marker=mk, ms=6, color=color, label=names[s])
        ax.fill_between(m["n"], m["lo"], m["hi"], color=color, alpha=0.12, lw=0)
    ax.annotate("before refinement", xy=(0, cmp_df[cmp_df.n_extra == 0]["f1"].mean()),
                xytext=(6, -14), textcoords="offset points", fontsize=8.5, color=INK2)
    ax.set(xlabel="Extra experiments after the initial 15", ylabel="Held-out F1 (SLO violation)",
           title="Rule accuracy before and after refinement")
    ax.legend(loc="lower right", fontsize=8.5)

    ax = axs[1]
    agg = cex_df.groupby(["iteration", "source"])[["tested", "counterexamples"]].mean().reset_index()
    srcs = [s for s in ("gp_disagreement", "boundary", "monotone") if s in set(agg.source)]
    labels = {"gp_disagreement": "GP disagreement", "boundary": "Solver boundary probe",
              "monotone": "Solver monotonicity witness"}
    colors = {"gp_disagreement": C1, "boundary": C2, "monotone": C3}
    its = sorted(agg.iteration.unique())
    width = 0.8 / max(len(srcs), 1)
    for k, s in enumerate(srcs):
        g = agg[agg.source == s].set_index("iteration").reindex(its).fillna(0)
        xs = np.array(its) + (k - (len(srcs) - 1) / 2) * width
        ax.bar(xs, g["counterexamples"], width=width - 0.04, color=colors[s], label=labels[s])
    ax.set(xlabel="CEGIS iteration", ylabel="Validated counterexamples (mean)",
           title="Counterexamples found per iteration", xticks=its)
    ax.legend(fontsize=8.5)
    fig.suptitle("8. Constraint refinement with CEGIS — monotone synthesiser, simulated testbed, "
                 "exact solver (Z3 pending)", fontweight="bold", color=INK, fontsize=10.5)
    return _save(fig, out_dir, "08_constraint_refinement.png")
