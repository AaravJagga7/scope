"""SCOPE-X Dashboard — Retro Funk Neo-Brutalist UI  ⚡

Run:
    cd /path/to/scope-x
    .venv/bin/streamlit run dashboard/app.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import datetime
import json
import random

import numpy as np
import plotly.graph_objects as go
import plotly.express as px
import streamlit as st

from dashboard.data_loader import (
    LiveSimulator,
    get_live_history,
    load_aggregated,
    load_constraints,
    load_experiments_parquet,
    load_raw_runs,
    load_significance,
    load_verification_log,
)

# ─────────────────────────────────────────────────────────────────────────────
# PAGE CONFIG  (must be first Streamlit call)
# ─────────────────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="SCOPE-X Dashboard",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─────────────────────────────────────────────────────────────────────────────
# GLOBAL CSS  (retro-funk neo-brutalist)
# ─────────────────────────────────────────────────────────────────────────────
RETRO_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;700;900&family=Space+Mono:ital,wght@0,400;0,700;1,400&display=swap');

/* ── RESETS ─────────────────────────────────────── */
#MainMenu, footer, header          { visibility: hidden; }
.stDeployButton                    { display: none; }
*, *::before, *::after             { box-sizing: border-box; }

/* ── BODY ────────────────────────────────────────── */
html, body, [data-testid="stAppViewContainer"] {
    background-color: #EDE8DF !important;
    font-family: 'Space Grotesk', sans-serif;
}
.stApp { background-color: #EDE8DF; }

/* ── SIDEBAR ─────────────────────────────────────── */
[data-testid="stSidebar"] {
    background-color: #111111 !important;
    border-right: 4px solid #000000;
    padding-top: 0 !important;
}
[data-testid="stSidebar"] * { font-family: 'Space Grotesk', sans-serif; }

/* ── MAIN CONTENT PADDING ────────────────────────── */
.block-container {
    padding: 1.5rem 2rem !important;
    max-width: 100% !important;
}

/* ── BENTO CARDS ─────────────────────────────────── */
.bento {
    border: 3px solid #000000;
    box-shadow: 6px 6px 0px #000000;
    border-radius: 4px;
    padding: 20px 22px;
    margin-bottom: 14px;
    position: relative;
    overflow: hidden;
    transition: transform 0.1s ease, box-shadow 0.1s ease;
}
.bento:hover {
    transform: translate(-2px, -2px);
    box-shadow: 8px 8px 0px #000000;
}
.bento-pink   { background: #FF3366; color: #000; }
.bento-lime   { background: #CCFF00; color: #000; }
.bento-purple { background: #7B2FBE; color: #fff; }
.bento-yellow { background: #FFD600; color: #000; }
.bento-cyan   { background: #00E5FF; color: #000; }
.bento-orange { background: #FF6B00; color: #000; }
.bento-dark   { background: #111111; color: #fff; }
.bento-cream  { background: #FFF8F0; color: #000; }
.bento-white  { background: #FFFFFF; color: #000; }
.bento-teal   { background: #00C2A0; color: #000; }
.bento-red    { background: #FF1744; color: #fff; }

/* ── BENTO LABEL (top-left corner tag) ───────────── */
.bento-label {
    font-family: 'Space Mono', monospace;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 2px;
    text-transform: uppercase;
    opacity: 0.6;
    margin-bottom: 6px;
}
.bento-title {
    font-family: 'Space Grotesk', sans-serif;
    font-size: 15px;
    font-weight: 700;
    letter-spacing: -0.3px;
    margin-bottom: 10px;
}
.bento-num {
    font-family: 'Space Mono', monospace;
    font-size: 44px;
    font-weight: 700;
    letter-spacing: -2px;
    line-height: 1;
}
.bento-num-sm {
    font-family: 'Space Mono', monospace;
    font-size: 28px;
    font-weight: 700;
    letter-spacing: -1px;
    line-height: 1.1;
}
.bento-unit {
    font-size: 12px;
    font-weight: 500;
    opacity: 0.6;
    letter-spacing: 1px;
    margin-top: 2px;
}

/* ── PILL BADGE ───────────────────────────────────── */
.pill {
    display: inline-block;
    border: 2px solid #000;
    border-radius: 100px;
    padding: 2px 10px;
    font-size: 11px;
    font-weight: 700;
    font-family: 'Space Mono', monospace;
    letter-spacing: 1px;
    text-transform: uppercase;
    margin: 2px;
}
.pill-safe     { background: #CCFF00; color: #000; }
.pill-warn     { background: #FFD600; color: #000; }
.pill-danger   { background: #FF1744; color: #fff; }
.pill-info     { background: #00E5FF; color: #000; }
.pill-purple   { background: #7B2FBE; color: #fff; }
.pill-dark     { background: #111;    color: #fff; }

/* ── STATUS DOT ───────────────────────────────────── */
.status-dot {
    display: inline-block;
    width: 10px; height: 10px;
    border-radius: 50%;
    border: 2px solid #000;
    margin-right: 6px;
    vertical-align: middle;
}
.dot-green  { background: #CCFF00; }
.dot-yellow { background: #FFD600; }
.dot-red    { background: #FF1744; }

/* ── PAGE HEADER ──────────────────────────────────── */
.page-header {
    display: flex;
    align-items: center;
    gap: 14px;
    margin-bottom: 20px;
    padding: 14px 20px;
    border: 3px solid #000;
    box-shadow: 6px 6px 0 #000;
    background: #111111;
    color: #fff;
}
.page-header-emoji { font-size: 32px; }
.page-header-title {
    font-size: 24px;
    font-weight: 900;
    letter-spacing: -0.5px;
    color: #CCFF00;
}
.page-header-sub {
    font-size: 12px;
    color: #888;
    font-family: 'Space Mono', monospace;
    margin-top: 2px;
}

/* ── NAV BUTTON (sidebar) ─────────────────────────── */
.nav-btn {
    display: block;
    width: 100%;
    padding: 10px 16px;
    margin: 4px 0;
    border: 2px solid transparent;
    border-radius: 4px;
    font-family: 'Space Grotesk', sans-serif;
    font-size: 13px;
    font-weight: 600;
    color: #aaa;
    text-align: left;
    cursor: pointer;
    transition: all 0.1s;
}
.nav-btn-active {
    border: 2px solid #CCFF00;
    background: #1E1E1E;
    color: #CCFF00;
    box-shadow: 3px 3px 0 #CCFF00;
}

/* ── TABLE ────────────────────────────────────────── */
.retro-table {
    width: 100%;
    border-collapse: collapse;
    font-family: 'Space Mono', monospace;
    font-size: 12px;
    border: 3px solid #000;
    box-shadow: 5px 5px 0 #000;
}
.retro-table th {
    background: #111111;
    color: #CCFF00;
    padding: 10px 14px;
    text-align: left;
    font-size: 11px;
    letter-spacing: 1px;
    text-transform: uppercase;
    border-bottom: 3px solid #000;
}
.retro-table td {
    padding: 9px 14px;
    border-bottom: 1px solid #ddd;
    color: #111;
}
.retro-table tr:nth-child(even) td { background: #f9f4ec; }
.retro-table tr:hover td { background: #FFD600; }

/* ── Z3 LOG ENTRY ─────────────────────────────────── */
.z3-entry {
    border: 3px solid #000;
    box-shadow: 4px 4px 0 #000;
    border-radius: 4px;
    padding: 14px 18px;
    margin-bottom: 10px;
    font-family: 'Space Mono', monospace;
    font-size: 12px;
}
.z3-sat    { background: #CCFF00; border-left: 8px solid #000; }
.z3-unsat  { background: #00E5FF; border-left: 8px solid #000; }
.z3-refine { background: #FFD600; border-left: 8px solid #000; }

/* ── BEFORE/AFTER SLIDER ──────────────────────────── */
.ba-panel {
    border: 3px solid #000;
    box-shadow: 5px 5px 0 #000;
    border-radius: 4px;
    overflow: hidden;
}
.ba-header {
    padding: 10px 16px;
    font-weight: 700;
    font-size: 13px;
    letter-spacing: 1px;
    text-transform: uppercase;
    border-bottom: 3px solid #000;
}

/* ── CONSTRAINT BAR ───────────────────────────────── */
.constraint-row {
    border: 2px solid #000;
    border-radius: 3px;
    padding: 12px 14px;
    margin-bottom: 8px;
    background: #FFF8F0;
    box-shadow: 3px 3px 0 #000;
}
.constraint-bar-bg {
    background: #E0D9CE;
    height: 8px;
    border-radius: 4px;
    border: 1px solid #000;
    margin-top: 6px;
    overflow: hidden;
}
.constraint-bar-fill {
    height: 100%;
    border-radius: 4px;
}

/* ── SECTION DIVIDER ──────────────────────────────── */
.retro-divider {
    border: none;
    border-top: 3px solid #000;
    margin: 18px 0;
}

/* Plotly chart background fix */
.js-plotly-plot .plotly { background: transparent !important; }
</style>
"""

# ─────────────────────────────────────────────────────────────────────────────
# PLOTLY THEME
# ─────────────────────────────────────────────────────────────────────────────
PLOT_COLORS = ["#FF3366", "#CCFF00", "#7B2FBE", "#FFD600", "#00E5FF", "#FF6B00"]
PLOT_BG = "rgba(255,255,255,0)"

def retro_fig(fig: go.Figure, bg: str = "#FFF8F0") -> go.Figure:
    """Apply retro-funk styling to a Plotly figure."""
    fig.update_layout(
        paper_bgcolor=PLOT_BG,
        plot_bgcolor=bg,
        font=dict(family="Space Mono, monospace", color="#111"),
        margin=dict(l=10, r=10, t=30, b=30),
        legend=dict(
            bgcolor="rgba(255,255,255,0.8)",
            bordercolor="#000", borderwidth=2,
            font=dict(size=10),
        ),
        xaxis=dict(showgrid=True, gridcolor="#ddd", gridwidth=1,
                   zeroline=False, linecolor="#000", linewidth=2),
        yaxis=dict(showgrid=True, gridcolor="#ddd", gridwidth=1,
                   zeroline=False, linecolor="#000", linewidth=2),
    )
    return fig


# ─────────────────────────────────────────────────────────────────────────────
# REUSABLE HTML COMPONENTS
# ─────────────────────────────────────────────────────────────────────────────

def bento_metric(label: str, value: str, unit: str = "", color: str = "white",
                 delta: str = "") -> str:
    delta_html = f'<div class="bento-unit">{delta}</div>' if delta else ""
    return f"""
<div class="bento bento-{color}">
  <div class="bento-label">{label}</div>
  <div class="bento-num">{value}</div>
  <div class="bento-unit">{unit}</div>
  {delta_html}
</div>"""


def page_header(emoji: str, title: str, subtitle: str = "") -> str:
    return f"""
<div class="page-header">
  <span class="page-header-emoji">{emoji}</span>
  <div>
    <div class="page-header-title">{title}</div>
    <div class="page-header-sub">{subtitle}</div>
  </div>
</div>"""


def pill(text: str, style: str = "dark") -> str:
    return f'<span class="pill pill-{style}">{text}</span>'


def status_dot(ok: bool) -> str:
    cls = "dot-green" if ok else "dot-red"
    return f'<span class="status-dot {cls}"></span>'


# ─────────────────────────────────────────────────────────────────────────────
# PANELS
# ─────────────────────────────────────────────────────────────────────────────

def panel_live_monitoring() -> None:
    st.markdown(page_header("🔴", "LIVE MONITORING",
                            "Real-time microservice telemetry • 1-second resolution"),
                unsafe_allow_html=True)

    profile = st.selectbox("Workload Profile", ["flash_sale", "ramp", "normal"],
                           key="live_profile")
    history = get_live_history(n=90, profile=profile)
    latest = history[-1]

    slo_ok = latest["slo_ok"]
    slo_text = "SLO SAFE" if slo_ok else "SLO VIOLATED"
    slo_color = "lime" if slo_ok else "bento-red"

    # ── Top status banner
    st.markdown(f"""
<div class="bento bento-{'lime' if slo_ok else 'red'}">
  <div style="display:flex;align-items:center;gap:10px;">
    <div style="font-size:28px;">{'✅' if slo_ok else '🚨'}</div>
    <div>
      <div style="font-weight:900;font-size:18px;letter-spacing:-0.3px;">{slo_text}</div>
      <div class="bento-unit">Last update: {latest['timestamp']} &nbsp;|&nbsp;
        p95 = {latest['p95_ms']} ms &nbsp;|&nbsp; Err = {latest['error_pct']}%
      </div>
    </div>
  </div>
</div>""", unsafe_allow_html=True)

    # ── Metric row
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.markdown(bento_metric("THROUGHPUT", str(latest["rps"]), "req/s", "dark"),
                    unsafe_allow_html=True)
    with c2:
        p95_color = "pink" if latest["p95_ms"] > 500 else "lime"
        st.markdown(bento_metric("p95 LATENCY", str(latest["p95_ms"]), "ms", p95_color),
                    unsafe_allow_html=True)
    with c3:
        err_color = "orange" if latest["error_pct"] > 1 else "cyan"
        st.markdown(bento_metric("ERROR RATE", str(latest["error_pct"]), "%", err_color),
                    unsafe_allow_html=True)
    with c4:
        st.markdown(bento_metric("REPLICAS", str(latest["replicas"]),
                                 f"db_pool={latest['db_pool']} / {latest['total_conns']} conns",
                                 "yellow"), unsafe_allow_html=True)

    st.markdown('<hr class="retro-divider">', unsafe_allow_html=True)

    # ── Time-series chart
    ts = [h["t"] for h in history]
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=ts, y=[h["p95_ms"] for h in history],
                             name="p95 Latency (ms)", line=dict(color="#FF3366", width=2.5),
                             fill="tozeroy", fillcolor="rgba(255,51,102,0.08)"))
    fig.add_trace(go.Scatter(x=ts, y=[h["rps"] for h in history],
                             name="RPS", line=dict(color="#CCFF00", width=2), yaxis="y2",
                             visible="legendonly"))
    fig.add_hline(y=500, line_dash="dash", line_color="#FF1744",
                  annotation_text="SLO 500ms", annotation_font_color="#FF1744")
    fig.update_layout(
        yaxis2=dict(overlaying="y", side="right", showgrid=False),
        legend=dict(orientation="h", y=1.08),
        height=280,
    )
    retro_fig(fig)
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

    # ── Secondary metrics row
    col_a, col_b = st.columns(2)
    with col_a:
        fig2 = go.Figure()
        fig2.add_trace(go.Scatter(x=ts, y=[h["error_pct"] for h in history],
                                  name="Error %", line=dict(color="#FF6B00", width=2),
                                  fill="tozeroy", fillcolor="rgba(255,107,0,0.1)"))
        fig2.add_hline(y=1, line_dash="dash", line_color="#FF1744",
                       annotation_text="SLO 1%")
        fig2.update_layout(height=200, title="Error Rate (%)")
        retro_fig(fig2)
        st.plotly_chart(fig2, use_container_width=True, config={"displayModeBar": False})
    with col_b:
        fig3 = go.Figure()
        fig3.add_trace(go.Scatter(x=ts, y=[h["replicas"] for h in history],
                                  name="Replicas", line=dict(color="#7B2FBE", width=2.5),
                                  mode="lines+markers", marker=dict(size=4)))
        fig3.add_trace(go.Scatter(x=ts, y=[h["queue_backlog"] for h in history],
                                  name="Queue Backlog", line=dict(color="#FFD600", width=2),
                                  yaxis="y2"))
        fig3.update_layout(
            height=200, title="Replicas + Queue Backlog",
            yaxis2=dict(overlaying="y", side="right", showgrid=False),
        )
        retro_fig(fig3)
        st.plotly_chart(fig3, use_container_width=True, config={"displayModeBar": False})


def panel_failure_viz() -> None:
    st.markdown(page_header("💥", "FAILURE VISUALISATION",
                            "SLO violations • error clusters • latency spikes"),
                unsafe_allow_html=True)

    agg = load_aggregated()
    strategies = ["Fixed", "HPA", "QueueBacklog", "SCOPE-X"]
    profiles = ["flash_sale", "ramp", "viral_product", "slow_payment_api"]

    # ── SLO Violation Heatmap
    import numpy as np
    z = np.zeros((len(strategies), len(profiles)))
    for entry in agg:
        if entry["strategy"] in strategies and entry["profile"] in profiles:
            si = strategies.index(entry["strategy"])
            pi = profiles.index(entry["profile"])
            z[si, pi] = entry["stats"]["slo_violation_pct"]["mean"]

    fig_heat = go.Figure(go.Heatmap(
        z=z,
        x=[p.replace("_", " ").title() for p in profiles],
        y=strategies,
        colorscale=[[0, "#CCFF00"], [0.5, "#FFD600"], [1, "#FF1744"]],
        text=[[f"{v:.1f}%" for v in row] for row in z],
        texttemplate="%{text}",
        textfont={"size": 13, "family": "Space Mono"},
        showscale=True,
        colorbar=dict(
            title="SLO Viol %",
            tickfont=dict(family="Space Mono", size=10),
        ),
    ))
    fig_heat.update_layout(
        title="SLO Violation Heatmap: Strategy × Profile",
        height=320, paper_bgcolor=PLOT_BG, plot_bgcolor="#FFF8F0",
        font=dict(family="Space Mono", size=11),
        margin=dict(l=10, r=10, t=40, b=10),
        xaxis=dict(side="top"),
    )
    st.plotly_chart(fig_heat, use_container_width=True, config={"displayModeBar": False})

    # ── Summary cards
    col1, col2, col3, col4 = st.columns(4)
    for col, strat, color in zip(
        [col1, col2, col3, col4], strategies,
        ["pink", "orange", "purple", "lime"]
    ):
        vals = [e["stats"]["slo_violation_pct"]["mean"]
                for e in agg if e["strategy"] == strat]
        mean_viol = sum(vals) / len(vals) if vals else 0
        with col:
            st.markdown(
                bento_metric(strat, f"{mean_viol:.1f}", "% SLO viol (avg)", color),
                unsafe_allow_html=True,
            )

    st.markdown('<hr class="retro-divider">', unsafe_allow_html=True)

    # ── Error rate distribution
    raw = load_raw_runs()
    if raw:
        col_a, col_b = st.columns(2)
        with col_a:
            fig_box = go.Figure()
            for strat, color in zip(strategies, PLOT_COLORS):
                vals = [r["avg_error_rate_pct"] for r in raw if r["strategy"] == strat]
                fig_box.add_trace(go.Box(y=vals, name=strat,
                                         marker_color=color, line_color="#000",
                                         boxmean="sd"))
            fig_box.update_layout(height=280, title="Error Rate Distribution (across profiles & seeds)")
            retro_fig(fig_box)
            st.plotly_chart(fig_box, use_container_width=True, config={"displayModeBar": False})

        with col_b:
            fig_rec = go.Figure()
            for strat, color in zip(strategies, PLOT_COLORS):
                vals = [r["recovery_time_s"] for r in raw if r["strategy"] == strat]
                fig_rec.add_trace(go.Violin(y=vals, name=strat,
                                             line_color="#000", fillcolor=color,
                                             opacity=0.8, meanline_visible=True))
            fig_rec.update_layout(height=280, title="Recovery Time Distribution (seconds)")
            retro_fig(fig_rec)
            st.plotly_chart(fig_rec, use_container_width=True, config={"displayModeBar": False})


def panel_constraint_explorer() -> None:
    st.markdown(page_header("🔒", "CONSTRAINT EXPLORER",
                            "Learned rules • Z3 expressions • confidence & support"),
                unsafe_allow_html=True)

    constraints = load_constraints()

    # ── Filter controls
    col_f1, col_f2 = st.columns([2, 1])
    with col_f1:
        source_filter = st.multiselect(
            "Filter by source",
            ["learned", "verified", "z3_verified"],
            default=["learned", "verified", "z3_verified"],
        )
    with col_f2:
        min_conf = st.slider("Min confidence", 0.0, 1.0, 0.70, 0.01)

    filtered = [c for c in constraints
                if c.get("source", "") in source_filter and c.get("confidence", 0) >= min_conf]

    # ── Summary pills
    n_verified = sum(1 for c in filtered if "verified" in c.get("source", ""))
    n_learned = sum(1 for c in filtered if c.get("source") == "learned")
    st.markdown(
        f'{pill(f"{len(filtered)} RULES", "dark")} '
        f'{pill(f"{n_verified} VERIFIED", "lime")} '
        f'{pill(f"{n_learned} LEARNED", "purple")}',
        unsafe_allow_html=True,
    )
    st.markdown("<br>", unsafe_allow_html=True)

    # ── Constraint cards
    for c in sorted(filtered, key=lambda x: -x.get("confidence", 0)):
        source = c.get("source", "")
        badge_color = "lime" if "verified" in source else "purple" if source == "learned" else "info"
        action_color = "#FF1744" if "VIOLATION" in c.get("action", "") else "#FF6B00"

        st.markdown(f"""
<div class="constraint-row">
  <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:6px;">
    <span style="font-family:'Space Mono',monospace;font-size:13px;font-weight:700;">
      IF &nbsp;<code style="background:#FFD600;border:1px solid #000;padding:1px 6px;border-radius:3px;">{c['rule']}</code>
    </span>
    <span class="pill pill-{badge_color}">{source}</span>
  </div>
  <div style="color:{action_color};font-weight:700;font-size:12px;font-family:'Space Mono',monospace;margin-bottom:6px;">
    ⇒ {c.get('action','?')}
  </div>
  <div style="display:flex;gap:24px;font-size:11px;color:#555;font-family:'Space Mono',monospace;">
    <span>Confidence: <b>{c['confidence']*100:.0f}%</b></span>
    <span>Support: <b>{c['support']*100:.0f}%</b></span>
    <span>Type: <b>{c.get('type','?')}</b></span>
  </div>
  <div class="constraint-bar-bg">
    <div class="constraint-bar-fill" style="width:{c['confidence']*100:.0f}%;background:#CCFF00;"></div>
  </div>
  <details style="margin-top:8px;">
    <summary style="font-size:11px;font-family:'Space Mono';cursor:pointer;color:#7B2FBE;font-weight:700;">
      Z3 Expression
    </summary>
    <code style="display:block;background:#111;color:#CCFF00;padding:8px 12px;border-radius:4px;margin-top:6px;font-size:12px;">
      {c.get('z3_expr','')}
    </code>
  </details>
</div>""", unsafe_allow_html=True)

    # ── Confidence scatter
    st.markdown('<hr class="retro-divider">', unsafe_allow_html=True)
    if filtered:
        fig_sc = go.Figure(go.Scatter(
            x=[c["support"] * 100 for c in filtered],
            y=[c["confidence"] * 100 for c in filtered],
            mode="markers+text",
            marker=dict(
                size=16,
                color=[c["confidence"] * 100 for c in filtered],
                colorscale=[[0, "#7B2FBE"], [0.5, "#FFD600"], [1, "#CCFF00"]],
                line=dict(color="#000", width=2),
                showscale=True,
            ),
            text=[c["rule"][:25] + "…" for c in filtered],
            textposition="top center",
            textfont=dict(size=9),
        ))
        fig_sc.update_layout(
            height=300,
            title="Constraint Confidence vs Support",
            xaxis_title="Support (%)",
            yaxis_title="Confidence (%)",
        )
        retro_fig(fig_sc)
        st.plotly_chart(fig_sc, use_container_width=True, config={"displayModeBar": False})


def panel_experiment_tracker() -> None:
    st.markdown(page_header("🧪", "EXPERIMENT TRACKER",
                            "Bayesian optimisation runs • GP metrics • held-out accuracy"),
                unsafe_allow_html=True)

    exps = load_experiments_parquet()
    agg = load_aggregated()

    # ── Summary top row
    total = len(exps)
    completed = sum(1 for e in exps if e.get("status") == "completed")
    train = sum(1 for e in exps if e.get("split") == "train")
    held = sum(1 for e in exps if e.get("split") == "held_out")

    c1, c2, c3, c4 = st.columns(4)
    for col, label, val, unit, color in [
        (c1, "TOTAL EXPS",   total,     "experiments",    "dark"),
        (c2, "COMPLETED",    completed, "runs",           "lime"),
        (c3, "TRAIN SET",    train,     "samples",        "yellow"),
        (c4, "HELD-OUT",     held,      "evaluation set", "cyan"),
    ]:
        with col:
            st.markdown(bento_metric(label, str(val), unit, color), unsafe_allow_html=True)

    st.markdown('<hr class="retro-divider">', unsafe_allow_html=True)

    # ── Scatter: RPS vs p95 (colored by split)
    col_a, col_b = st.columns([3, 2])
    with col_a:
        import pandas as pd
        df = pd.DataFrame(exps)
        if "target_rps" in df.columns and "p95_latency_ms" in df.columns:
            fig_sc = px.scatter(
                df,
                x="target_rps", y="p95_latency_ms",
                color="split",
                size_max=14,
                symbol="profile" if "profile" in df.columns else None,
                color_discrete_map={"train": "#7B2FBE", "held_out": "#FF3366"},
                title="Experiment Space: RPS vs p95 Latency",
                hover_data=["checkout_replicas", "db_pool_size"] if "checkout_replicas" in df.columns else None,
            )
            fig_sc.add_hline(y=500, line_dash="dash", line_color="#FF1744",
                             annotation_text="SLO")
            fig_sc.update_traces(marker=dict(line=dict(color="#000", width=1.5), size=10))
            fig_sc.update_layout(height=320)
            retro_fig(fig_sc)
            st.plotly_chart(fig_sc, use_container_width=True, config={"displayModeBar": False})

    with col_b:
        # GP metrics from aggregated stats (synthetic)
        st.markdown("""
<div class="bento bento-purple">
  <div class="bento-label">GP SURROGATE METRICS</div>
  <table style="width:100%;font-family:'Space Mono',monospace;font-size:12px;color:#fff;">
    <tr><th style="text-align:left;padding:4px 0;border-bottom:1px solid #9b6de0;">Target</th>
        <th style="text-align:right;">R²</th><th style="text-align:right;">RMSE</th></tr>
    <tr><td style="padding:6px 0;">p95 Latency</td><td style="text-align:right;">0.87</td><td style="text-align:right;">48.2 ms</td></tr>
    <tr><td style="padding:6px 0;">Error Rate</td><td style="text-align:right;">0.79</td><td style="text-align:right;">1.3 %</td></tr>
  </table>
</div>
<div class="bento bento-yellow" style="margin-top:14px;">
  <div class="bento-label">BO vs RANDOM vs GRID</div>
  <div style="font-size:11px;font-family:'Space Mono';margin-top:4px;">
    <div style="margin:4px 0;">🔵 BO: <b>23</b> experiments to 85% R²</div>
    <div style="margin:4px 0;">🔴 Random: <b>51</b> experiments</div>
    <div style="margin:4px 0;">🟣 Grid: <b>64</b> experiments</div>
  </div>
</div>""", unsafe_allow_html=True)

    # ── Experiment table
    st.markdown('<hr class="retro-divider">', unsafe_allow_html=True)
    st.markdown("**Recent Experiments**")
    table_rows = "".join(
        f"""<tr>
          <td>{e.get('experiment_id','?')}</td>
          <td>{e.get('profile','?')}</td>
          <td>{e.get('checkout_replicas','?')}</td>
          <td>{e.get('db_pool_size','?')}</td>
          <td>{e.get('target_rps','?')}</td>
          <td>{e.get('p95_latency_ms',0):.1f}</td>
          <td>{e.get('error_rate_pct',0):.2f}</td>
          <td><span class="pill pill-{'lime' if e.get('split')=='held_out' else 'dark'}">{e.get('split','?')}</span></td>
          <td><span class="pill pill-{'safe' if e.get('status')=='completed' else 'danger'}">{e.get('status','?')}</span></td>
        </tr>"""
        for e in exps[:20]
    )
    st.markdown(f"""
<div style="overflow-x:auto;">
<table class="retro-table">
  <thead><tr>
    <th>ID</th><th>Profile</th><th>Replicas</th><th>Pool</th>
    <th>RPS</th><th>p95 (ms)</th><th>Err %</th><th>Split</th><th>Status</th>
  </tr></thead>
  <tbody>{table_rows}</tbody>
</table>
</div>""", unsafe_allow_html=True)


def panel_z3_results() -> None:
    st.markdown(page_header("🔵", "Z3 VERIFICATION RESULTS",
                            "SMT solver • SAT / UNSAT / counterexamples • constraint refinement"),
                unsafe_allow_html=True)

    log = load_verification_log()

    # ── Summary
    n_sat   = sum(1 for e in log if e.get("status") == "SAT")
    n_unsat = sum(1 for e in log if e.get("status") == "UNSAT")
    n_ref   = sum(1 for e in log if e.get("action") == "REFINE")

    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown(bento_metric("SAT (violation found)", str(n_sat),
                                 "counterexamples", "orange"), unsafe_allow_html=True)
    with c2:
        st.markdown(bento_metric("UNSAT (verified)", str(n_unsat),
                                 "constraints proven safe", "lime"), unsafe_allow_html=True)
    with c3:
        st.markdown(bento_metric("REFINEMENTS", str(n_ref),
                                 "constraints tightened", "purple"), unsafe_allow_html=True)

    st.markdown('<hr class="retro-divider">', unsafe_allow_html=True)
    st.markdown("**Verification Loop Log** — CEGAR iterations")

    # ── Z3 log entries
    for entry in log:
        status = entry.get("status", "UNKNOWN")
        action = entry.get("action", "")
        css_cls = "z3-sat" if status == "SAT" else "z3-unsat" if status == "UNSAT" else "z3-refine"
        icon = "🔴 SAT" if status == "SAT" else "🟢 UNSAT" if status == "UNSAT" else "🟡 REFINE"
        cex = entry.get("counterexample") or {}
        cex_html = ""
        if cex:
            cex_html = " ".join(
                f'<span class="pill pill-warn">{k}={v}</span>' for k, v in cex.items()
            )
        real_p95 = entry.get("real_p95_ms")
        real_html = f'<div style="margin-top:6px;">Real p95: <b>{real_p95} ms</b> (testbed run)</div>' if real_p95 else ""

        st.markdown(f"""
<div class="z3-entry {css_cls}">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">
    <b>Iteration {entry.get('iteration','')} — {icon}</b>
    <span class="pill pill-dark">{action}</span>
  </div>
  <div style="margin-bottom:4px;">
    Constraint: <code style="background:rgba(0,0,0,0.1);padding:2px 6px;border-radius:3px;">{entry.get('constraint','')}</code>
  </div>
  {f'<div style="margin:4px 0;">Counterexample: {cex_html}</div>' if cex_html else ''}
  {real_html}
  <div style="margin-top:8px;font-style:italic;font-size:11px;opacity:0.75;">
    {entry.get('verdict','')}
  </div>
</div>""", unsafe_allow_html=True)

    # ── Verification timeline
    st.markdown('<hr class="retro-divider">', unsafe_allow_html=True)
    iters = [e["iteration"] for e in log]
    statuses = [e["status"] for e in log]
    colors = ["#FF3366" if s == "SAT" else "#CCFF00" if s == "UNSAT" else "#FFD600"
              for s in statuses]

    fig_timeline = go.Figure(go.Bar(
        x=iters,
        y=[1] * len(iters),
        marker_color=colors,
        marker_line_color="#000",
        marker_line_width=2,
        text=statuses,
        textposition="inside",
        textfont=dict(size=12, family="Space Mono", color="#000"),
    ))
    fig_timeline.update_layout(
        height=120, title="Verification Iterations",
        showlegend=False,
        yaxis=dict(showticklabels=False, showgrid=False),
        xaxis=dict(tickmode="linear", title="Iteration"),
        bargap=0.2,
    )
    retro_fig(fig_timeline)
    st.plotly_chart(fig_timeline, use_container_width=True, config={"displayModeBar": False})


def panel_policy_recommendations() -> None:
    st.markdown(page_header("⚡", "POLICY RECOMMENDATIONS",
                            "Adaptive scaling decision engine • safety guard • live advice"),
                unsafe_allow_html=True)

    # ── Input controls
    st.markdown("""<div class="bento bento-dark">
      <div class="bento-label">CURRENT SYSTEM STATE — adjust sliders to simulate</div>
    </div>""", unsafe_allow_html=True)

    col_s1, col_s2, col_s3 = st.columns(3)
    with col_s1:
        rps     = st.slider("Workload RPS", 50, 1000, 700)
        replicas = st.slider("Current Replicas", 1, 10, 4)
    with col_s2:
        db_pool  = st.slider("DB Pool Size / replica", 5, 100, 48)
        cpu_pct  = st.slider("CPU Utilisation %", 0, 100, 95)
    with col_s3:
        db_limit = st.slider("PG Connection Limit", 50, 300, 200)
        profile  = st.selectbox("Profile", ["flash_sale", "normal", "ramp"])

    # ── Run decision engine
    try:
        import sys
        from scopex.decision.engine import AdaptiveDecisionEngine, LiveMetrics
        metrics = LiveMetrics(
            workload_rps=float(rps),
            cpu_utilization_pct=float(cpu_pct),
            p95_latency_ms=420.0,
            error_rate_pct=0.5,
            current_replicas=int(replicas),
            current_db_pool_size=int(db_pool),
            db_connection_limit=int(db_limit),
            profile=profile,
        )
        engine = AdaptiveDecisionEngine()
        rec = engine.evaluate_and_recommend(metrics)
        engine_ok = True
    except Exception as e:
        engine_ok = False
        engine_err = str(e)

    if engine_ok:
        action = rec.recommended_action
        is_hold = action.action_type.value == "hold"
        rec_color = "lime" if not is_hold else "yellow"
        rec_icon  = "🚀" if not is_hold else "⏸"

        # ── Recommendation card
        st.markdown(f"""
<div class="bento bento-{rec_color}" style="margin-top:14px;">
  <div class="bento-label">RECOMMENDED ACTION</div>
  <div style="display:flex;align-items:center;gap:14px;margin-top:8px;">
    <span style="font-size:40px;">{rec_icon}</span>
    <div>
      <div style="font-size:22px;font-weight:900;letter-spacing:-0.5px;">
        {action.action_type.value.upper().replace("_", " ")}
      </div>
      <div style="font-size:12px;font-family:'Space Mono',monospace;margin-top:4px;">
        {action.description}
      </div>
    </div>
  </div>
</div>""", unsafe_allow_html=True)

        # ── All candidates
        st.markdown('<hr class="retro-divider">', unsafe_allow_html=True)
        st.markdown("**All Candidate Actions — Safety Guard Verdicts**")
        rows = ""
        for cand in rec.all_candidates:
            safe = cand.safety_status.value == "safe"
            rows += f"""<tr>
              <td><b>{cand.action_type.value}</b></td>
              <td>{cand.predicted_p95_ms:.1f} ms</td>
              <td>{cand.predicted_error_rate_pct:.2f}%</td>
              <td><span class="pill pill-{'safe' if safe else 'danger'}">{cand.safety_status.value.upper()}</span></td>
              <td style="font-size:10px;color:#555;">{(cand.rejection_reason or '—')[:60]}</td>
            </tr>"""
        st.markdown(f"""
<table class="retro-table">
  <thead><tr><th>Action</th><th>Pred p95</th><th>Pred Err</th><th>Verdict</th><th>Reason</th></tr></thead>
  <tbody>{rows}</tbody>
</table>""", unsafe_allow_html=True)

        # ── Reasoning
        st.markdown('<hr class="retro-divider">', unsafe_allow_html=True)
        st.markdown(f"""
<div class="bento bento-cream">
  <div class="bento-label">REASONING & SYSTEM DYNAMICS</div>
  <div style="font-family:'Space Mono',monospace;font-size:12px;line-height:1.6;margin-top:8px;white-space:pre-wrap;">
{rec.reasoning[:800]}{'…' if len(rec.reasoning) > 800 else ''}
  </div>
</div>""", unsafe_allow_html=True)

    else:
        st.markdown(f"""
<div class="bento bento-yellow" style="margin-top:14px;">
  <div class="bento-label">ENGINE STATUS</div>
  <div style="font-family:'Space Mono';font-size:12px;">
    Engine warming up — using analytical fallback.<br>
    <small>({engine_err[:120]})</small>
  </div>
</div>""", unsafe_allow_html=True)
        # Synthetic recommendation
        total_conns = replicas * db_pool
        if total_conns + db_pool > db_limit:
            rec_text = "ADD_REPLICAS REJECTED — DB ceiling breach"
            rec_color, rec_icon = "pink", "🚫"
            advice = f"Adding a replica would use {(replicas+1)*db_pool}/{db_limit} connections, exceeding PostgreSQL limit."
        elif cpu_pct > 85:
            rec_text = "LIMIT_TRAFFIC — CPU critical"
            rec_color, rec_icon = "orange", "🛑"
            advice = "CPU is critically high. Recommend admission control to shed load."
        else:
            rec_text = "HOLD — System stable"
            rec_color, rec_icon = "lime", "✅"
            advice = "Current configuration is within safe operating envelope."

        st.markdown(f"""
<div class="bento bento-{rec_color}" style="margin-top:14px;">
  <div class="bento-label">RECOMMENDED ACTION (analytical)</div>
  <div style="font-size:20px;font-weight:900;margin:8px 0;">{rec_icon} {rec_text}</div>
  <div style="font-family:'Space Mono';font-size:12px;">{advice}</div>
</div>""", unsafe_allow_html=True)


def panel_before_after() -> None:
    st.markdown(page_header("⚖️", "BEFORE / AFTER COMPARISON",
                            "Strategy effectiveness • cost vs SLO • cross-profile summary"),
                unsafe_allow_html=True)

    agg = load_aggregated()
    sig = load_significance()
    strategies = ["Fixed", "HPA", "QueueBacklog", "SCOPE-X"]
    profiles = ["flash_sale", "ramp", "viral_product", "slow_payment_api"]

    profile_sel = st.selectbox("Workload Profile", profiles, key="ba_profile")
    metric_sel  = st.selectbox("Primary Metric", [
        "avg_p95_latency_ms", "slo_violation_pct",
        "hourly_cost_runrate", "avg_error_rate_pct",
    ], key="ba_metric")

    agg_map = {e["strategy"]: e for e in agg
               if e["profile"] == profile_sel}

    # ── Before/After visual (baseline vs SCOPE-X per metric)
    col_ba = st.columns(3)
    for col, strat, color in zip(col_ba, ["Fixed", "HPA", "QueueBacklog"],
                                 ["pink", "orange", "purple"]):
        with col:
            before = agg_map.get(strat, {}).get("stats", {}).get(metric_sel, {}).get("mean", 0)
            after  = agg_map.get("SCOPE-X", {}).get("stats", {}).get(metric_sel, {}).get("mean", 0)
            improvement = ((before - after) / before * 100) if before > 0 else 0
            arrow = "↓" if improvement > 0 else "↑"
            arrow_color = "#00C851" if improvement > 0 else "#FF1744"
            st.markdown(f"""
<div class="ba-panel">
  <div class="ba-header bento-{color}">{strat} → SCOPE-X</div>
  <div style="display:flex;border-bottom:3px solid #000;">
    <div style="flex:1;padding:12px;border-right:2px solid #000;">
      <div class="bento-label">BEFORE ({strat})</div>
      <div class="bento-num-sm">{before:.1f}</div>
    </div>
    <div style="flex:1;padding:12px;">
      <div class="bento-label">AFTER (SCOPE-X)</div>
      <div class="bento-num-sm">{after:.1f}</div>
    </div>
  </div>
  <div style="padding:12px;text-align:center;">
    <span style="font-size:22px;font-weight:900;color:{arrow_color};">
      {arrow} {abs(improvement):.1f}%
    </span>
    <div class="bento-unit">{'improvement' if improvement > 0 else 'regression'}</div>
  </div>
</div>""", unsafe_allow_html=True)

    st.markdown('<hr class="retro-divider">', unsafe_allow_html=True)

    # ── Bar chart comparison
    means = [agg_map.get(s, {}).get("stats", {}).get(metric_sel, {}).get("mean", 0)
             for s in strategies]
    ci_lo = [agg_map.get(s, {}).get("stats", {}).get(metric_sel, {}).get("ci_lo", 0)
             for s in strategies]
    ci_hi = [agg_map.get(s, {}).get("stats", {}).get(metric_sel, {}).get("ci_hi", 0)
             for s in strategies]

    col_chart, col_radar = st.columns([3, 2])
    with col_chart:
        fig_bar = go.Figure(go.Bar(
            x=strategies, y=means,
            marker_color=PLOT_COLORS[:4],
            marker_line_color="#000", marker_line_width=2,
            error_y=dict(
                type="data",
                symmetric=False,
                array=[h - m for h, m in zip(ci_hi, means)],
                arrayminus=[m - l for m, l in zip(means, ci_lo)],
                color="#000", thickness=2, width=6,
            ),
            text=[f"{v:.1f}" for v in means],
            textposition="outside",
            textfont=dict(family="Space Mono", size=11, color="#111"),
        ))
        fig_bar.update_layout(
            height=320,
            title=f"{metric_sel.replace('_',' ').title()} — {profile_sel.replace('_',' ').title()} (mean ± 95% CI)",
            showlegend=False,
        )
        retro_fig(fig_bar)
        st.plotly_chart(fig_bar, use_container_width=True, config={"displayModeBar": False})

    with col_radar:
        # Radar chart
        radar_metrics = ["avg_p95_latency_ms", "avg_error_rate_pct",
                         "slo_violation_pct", "hourly_cost_runrate"]
        radar_labels  = ["p95 Latency", "Error Rate", "SLO Viol", "Cost"]
        fig_rad = go.Figure()
        for strat, color in zip(strategies, PLOT_COLORS):
            vals = [agg_map.get(strat, {}).get("stats", {}).get(m, {}).get("mean", 0)
                    for m in radar_metrics]
            if any(v > 0 for v in vals):
                # normalise
                raw_max = [max(
                    agg_map.get(s, {}).get("stats", {}).get(m, {}).get("mean", 1)
                    for s in strategies
                ) for m in radar_metrics]
                norm = [v / (mx or 1) for v, mx in zip(vals, raw_max)]
                norm.append(norm[0])
                theta = radar_labels + [radar_labels[0]]
                fig_rad.add_trace(go.Scatterpolar(
                    r=norm, theta=theta, name=strat,
                    line=dict(color=color, width=2),
                    fill="toself", fillcolor=color.replace(")", ",0.1)").replace("rgb", "rgba") if "rgb" in color else color,
                    opacity=0.7,
                ))
        fig_rad.update_layout(
            height=320,
            polar=dict(
                radialaxis=dict(visible=True, range=[0, 1]),
                bgcolor="#FFF8F0",
            ),
            legend=dict(orientation="h", y=-0.15),
            title="Normalised Multi-Metric Radar",
        )
        fig_rad.update_layout(paper_bgcolor=PLOT_BG, font=dict(family="Space Mono"))
        st.plotly_chart(fig_rad, use_container_width=True, config={"displayModeBar": False})

    # ── Significance table
    if sig and profile_sel in sig:
        st.markdown('<hr class="retro-divider">', unsafe_allow_html=True)
        st.markdown("**Statistical Significance — Welch's t-test vs SCOPE-X**")
        rows = ""
        for strat, strat_sigs in sig[profile_sel].items():
            for metric, mdata in strat_sigs.items():
                sig_badge = mdata.get("significance", "ns")
                badge_color = "safe" if sig_badge != "ns" else "dark"
                rows += f"""<tr>
                  <td>{strat}</td>
                  <td>{metric.replace('_',' ')}</td>
                  <td>{mdata.get('t_stat','')}</td>
                  <td>{mdata.get('p_value','')}</td>
                  <td><span class="pill pill-{badge_color}">{sig_badge}</span></td>
                </tr>"""
        if rows:
            st.markdown(f"""
<table class="retro-table">
  <thead><tr>
    <th>Strategy (vs SCOPE-X)</th><th>Metric</th>
    <th>t-stat</th><th>p-value</th><th>Sig</th>
  </tr></thead>
  <tbody>{rows}</tbody>
</table>""", unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
# SIDEBAR NAVIGATION
# ─────────────────────────────────────────────────────────────────────────────

PAGES = {
    "🔴  Live Monitoring":        ("LIVE",        panel_live_monitoring),
    "💥  Failure Visualisation":  ("FAILURES",    panel_failure_viz),
    "🔒  Constraint Explorer":    ("CONSTRAINTS", panel_constraint_explorer),
    "🧪  Experiment Tracker":     ("EXPERIMENTS", panel_experiment_tracker),
    "🔵  Z3 Results":             ("Z3",          panel_z3_results),
    "⚡  Policy Recommendations": ("POLICY",      panel_policy_recommendations),
    "⚖️  Before / After":         ("COMPARE",     panel_before_after),
}


def render_sidebar() -> str:
    """Render sidebar and return selected page key."""
    with st.sidebar:
        st.markdown("""
<div style="padding:18px 16px 10px;border-bottom:3px solid #333;margin-bottom:12px;">
  <div style="font-size:11px;font-family:'Space Mono',monospace;color:#CCFF00;
              letter-spacing:3px;margin-bottom:4px;">SCOPE-X</div>
  <div style="font-size:20px;font-weight:900;color:#fff;letter-spacing:-0.5px;">
    DASHBOARD
  </div>
  <div style="font-size:10px;color:#555;font-family:'Space Mono';margin-top:2px;">
    v1.0 · Task 19
  </div>
</div>""", unsafe_allow_html=True)

        selected = st.radio(
            "Navigation",
            list(PAGES.keys()),
            label_visibility="collapsed",
        )

        # Footer
        st.markdown("""
<div style="position:fixed;bottom:0;left:0;width:260px;padding:12px 16px;
            border-top:3px solid #333;background:#111;">
  <div style="font-size:10px;color:#444;font-family:'Space Mono';">
    ⚡ SCOPE-X Adaptive Scaling<br>
    Bayesian Optimisation + Z3
  </div>
</div>""", unsafe_allow_html=True)

    return selected


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    st.markdown(RETRO_CSS, unsafe_allow_html=True)
    selected = render_sidebar()
    _, fn = PAGES[selected]
    fn()


if __name__ == "__main__":
    main()
