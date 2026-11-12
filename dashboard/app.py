"""SCOPE-X Dashboard — Clean Modern Soft-UI (Jobgio Aesthetic) ⚡

Sleek, modern, minimalist dashboard matching the Jobgio UI:
- Soft off-white / light slate background (#F4F5F9)
- Pure white cards with soft rounded corners (20px) and subtle elevation
- Vertical quick-stat pill cards with circular icons and bold metrics
- Dual-bar telemetry statistics and policy approval / action guard lists
- Clean tabular experiment management and interactive scaling controls
- 7 core panels: Live Monitoring, Failure Visualisation, Constraint Explorer,
  Experiment Tracker, Z3 Results, Policy Recommendations, Before/After Comparison.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from dashboard.data_loader import (
    get_live_history,
    load_aggregated,
    load_constraints,
    load_experiments_parquet,
    load_raw_runs,
    load_significance,
    load_verification_log,
)

# ─────────────────────────────────────────────────────────────────────────────
# PAGE CONFIG
# ─────────────────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="ScopeX · Systems Intelligence",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─────────────────────────────────────────────────────────────────────────────
# MODERN SOFT-UI CSS (Jobgio Inspired)
# ─────────────────────────────────────────────────────────────────────────────
JOBGIO_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&display=swap');

/* ── RESETS ─────────────────────────────────────────────── */
#MainMenu, footer, header { visibility: hidden !important; }
.stDeployButton { display: none !important; }
*, *::before, *::after { box-sizing: border-box; }

/* ── BODY & CANVAS ──────────────────────────────────────── */
html, body, [data-testid="stAppViewContainer"] {
    background-color: #F4F5F9 !important;
    font-family: 'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, sans-serif !important;
    color: #1A1D24 !important;
    -webkit-font-smoothing: antialiased;
}
.stApp { background-color: #F4F5F9 !important; }

/* ── SIDEBAR ────────────────────────────────────────────── */
[data-testid="stSidebar"] {
    background-color: #FFFFFF !important;
    border-right: 1px solid #EAEBEF !important;
    box-shadow: 2px 0 16px rgba(0, 0, 0, 0.02) !important;
    padding-top: 1.5rem !important;
}
[data-testid="stSidebar"] * {
    font-family: 'Plus Jakarta Sans', sans-serif !important;
}

/* ── MAIN CONTENT CONTAINER ─────────────────────────────── */
.block-container {
    padding: 1.8rem 2.4rem !important;
    max-width: 1440px !important;
    margin: 0 auto !important;
}

/* ── CARDS (Jobgio Soft White Style) ────────────────────── */
.job-card {
    background: #FFFFFF;
    border-radius: 20px;
    border: 1px solid #ECEEF3;
    box-shadow: 0 4px 18px rgba(0, 0, 0, 0.025);
    padding: 22px 24px;
    margin-bottom: 20px;
    transition: transform 0.15s ease, box-shadow 0.15s ease;
}
.job-card:hover {
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.04);
}

/* ── QUICK STATS PILL (Vertical cards on top) ───────────── */
.quick-stat-pill {
    background: #FFFFFF;
    border: 1px solid #EAECEF;
    border-radius: 22px;
    padding: 18px 14px;
    text-align: center;
    box-shadow: 0 4px 14px rgba(0, 0, 0, 0.02);
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    min-height: 140px;
    transition: all 0.15s ease;
}
.quick-stat-pill:hover {
    transform: translateY(-2px);
    box-shadow: 0 8px 20px rgba(0, 0, 0, 0.05);
}
.stat-icon-circle {
    width: 36px;
    height: 36px;
    border-radius: 50%;
    background: #F4F5F8;
    color: #4A4D58;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 15px;
    margin-bottom: 12px;
}
.stat-icon-highlight {
    background: #111111 !important;
    color: #FFFFFF !important;
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.18);
}
.stat-number {
    font-size: 24px;
    font-weight: 800;
    color: #111111;
    letter-spacing: -0.6px;
    line-height: 1.1;
    margin-bottom: 4px;
}
.stat-label {
    font-size: 11px;
    font-weight: 600;
    color: #8C8E9B;
    text-transform: capitalize;
}

/* ── PILL BADGES ────────────────────────────────────────── */
.badge-pill {
    display: inline-flex;
    align-items: center;
    padding: 4px 12px;
    border-radius: 100px;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.2px;
}
.badge-active { background: #E9F8EE; color: #108A00; }
.badge-pending { background: #F1F2F6; color: #4B4F59; }
.badge-safe { background: #EBF3FF; color: #1B65F1; }
.badge-warning { background: #FFF4E5; color: #D97706; }
.badge-danger { background: #FEECEB; color: #D92D20; }
.badge-dark { background: #111111; color: #FFFFFF; }

/* ── SIDEBAR NAVIGATION ITEMS ───────────────────────────── */
.nav-pill-active {
    background: #111111;
    color: #FFFFFF !important;
    border-radius: 100px;
    padding: 10px 18px;
    font-weight: 600;
    display: flex;
    align-items: center;
    gap: 12px;
    margin-bottom: 6px;
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.12);
}
.nav-pill-item {
    color: #6C727F;
    border-radius: 100px;
    padding: 10px 18px;
    font-weight: 500;
    display: flex;
    align-items: center;
    gap: 12px;
    margin-bottom: 4px;
    transition: background 0.15s ease, color 0.15s ease;
}
.nav-pill-item:hover {
    background: #F4F5F9;
    color: #111111;
}

/* ── CLEAN MODERN TABLE ─────────────────────────────────── */
.job-table {
    width: 100%;
    border-collapse: separate;
    border-spacing: 0;
    font-size: 13px;
    color: #1A1D24;
}
.job-table th {
    background: #FAFAFC;
    color: #717684;
    font-size: 11px;
    font-weight: 600;
    text-transform: capitalize;
    padding: 12px 16px;
    border-bottom: 1px solid #ECEEF3;
    letter-spacing: 0.3px;
}
.job-table th:first-child { border-top-left-radius: 12px; }
.job-table th:last-child { border-top-right-radius: 12px; }
.job-table td {
    padding: 14px 16px;
    border-bottom: 1px solid #F2F3F7;
    background: #FFFFFF;
}
.job-table tr:hover td {
    background-color: #FBFBFE;
}
.job-table tr:last-child td:first-child { border-bottom-left-radius: 12px; }
.job-table tr:last-child td:last-child { border-bottom-right-radius: 12px; }

/* ── ACTION LIST ITEM (Right card: Pending Approvals) ─────── */
.approval-item {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 12px 0;
    border-bottom: 1px solid #F2F3F7;
}
.approval-item:last-child {
    border-bottom: none;
}
.approval-icon-box {
    width: 38px;
    height: 38px;
    border-radius: 12px;
    background: #F5F6FA;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 16px;
    margin-right: 14px;
}

/* ── HEADINGS & SUBTEXT ─────────────────────────────────── */
.panel-title {
    font-size: 18px;
    font-weight: 700;
    color: #111111;
    letter-spacing: -0.4px;
    margin: 0;
}
.panel-subtitle {
    font-size: 12px;
    color: #8C8E9B;
    margin-top: 2px;
    margin-bottom: 0;
}

/* ── OPERATOR PROFILE (Sidebar bottom) ───────────────────── */
.sidebar-profile {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 14px 16px;
    background: #F8F9FC;
    border-radius: 16px;
    margin-top: 24px;
}
.sidebar-avatar {
    width: 38px;
    height: 38px;
    border-radius: 50%;
    background: #111111;
    color: #FFFFFF;
    display: flex;
    align-items: center;
    justify-content: center;
    font-weight: 700;
    font-size: 14px;
}
</style>
"""

# ─────────────────────────────────────────────────────────────────────────────
# PLOTLY THEME (Clean Black & Neutral Grays)
# ─────────────────────────────────────────────────────────────────────────────
def apply_jobgio_chart_theme(fig: go.Figure, height: int = 280) -> go.Figure:
    """Applies the Jobgio minimal black/light-gray palette to Plotly figures."""
    fig.update_layout(
        paper_bgcolor="rgba(255,255,255,0)",
        plot_bgcolor="rgba(255,255,255,0)",
        font={"family": "Plus Jakarta Sans, sans-serif", "color": "#4B4F59", "size": 11},
        margin={"l": 10, "r": 10, "t": 20, "b": 20},
        height=height,
        legend={
            "orientation": "h",
            "yanchor": "bottom",
            "y": 1.02,
            "xanchor": "right",
            "x": 1,
            "font": {"size": 11, "color": "#6C727F"},
        },
        xaxis={
            "showgrid": False,
            "zeroline": False,
            "linecolor": "#E5E7EB",
            "linewidth": 1,
            "tickfont": {"color": "#8C8E9B", "size": 10},
        },
        yaxis={
            "showgrid": True,
            "gridcolor": "#F0F1F5",
            "gridwidth": 1,
            "zeroline": False,
            "tickfont": {"color": "#8C8E9B", "size": 10},
        },
    )
    return fig


# ─────────────────────────────────────────────────────────────────────────────
# REUSABLE UI BLOCKS
# ─────────────────────────────────────────────────────────────────────────────
def render_quick_stats_header(profile: str = "flash_sale") -> None:
    """Renders the top 'Quick Stats' banner matching the Jobgio header."""
    history = get_live_history(n=10, profile=profile)
    latest = history[-1] if history else {}

    col_title, col_stats = st.columns([1.2, 4.8])
    with col_title:
        st.markdown(
            """
            <div style="padding-top: 10px;">
                <div style="font-size: 20px; font-weight: 800; color: #111111; letter-spacing: -0.5px;">Quick Stats</div>
                <div style="font-size: 12px; color: #8C8E9B; margin-top: 6px; line-height: 1.4;">
                    Your cluster telemetry for the current live period.
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with col_stats:
        s1, s2, s3, s4, s5, s6 = st.columns(6)
        stats_data = [
            ("⚡", f"{int(latest.get('rps', 700))}", "Target RPS", False),
            ("📦", f"{latest.get('replicas', 4)}", "Replicas", False),
            ("🗄️", f"{latest.get('db_pool', 48)}", "DB Pool", True),  # Highlighted card!
            ("🔗", f"{latest.get('total_conns', 192)}", "DB Conns", False),
            ("🎯", "0.6%", "SLO Viol.", False),
            ("⏱️", f"{int(latest.get('p95_ms', 348))}ms", "p95 Latency", False),
        ]
        for col, (icon, num, label, is_highlighted) in zip(
            [s1, s2, s3, s4, s5, s6], stats_data, strict=False
        ):
            with col:
                highlight_cls = "stat-icon-highlight" if is_highlighted else ""
                st.markdown(
                    f"""
                    <div class="quick-stat-pill">
                        <div class="stat-icon-circle {highlight_cls}">{icon}</div>
                        <div class="stat-number">{num}</div>
                        <div class="stat-label">{label}</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
    st.markdown("<div style='height: 18px;'></div>", unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
# PANEL 1: LIVE MONITORING (Jobgio Main View)
# ─────────────────────────────────────────────────────────────────────────────
def panel_live_monitoring() -> None:
    profile = st.selectbox(
        "Traffic Profile",
        ["flash_sale", "ramp", "normal"],
        index=0,
        key="live_prof_sel",
    )
    render_quick_stats_header(profile=profile)

    col_chart, col_approvals = st.columns([2.8, 1.6])

    # Left: Dual-bar statistics chart (Offered RPS vs Admitted RPS)
    with col_chart:
        history = get_live_history(n=24, profile=profile)
        time_steps = [f"t+{h['t']}s" for h in history]
        rps_offered = [h["rps"] for h in history]
        admitted = [
            round(h["rps"] * (1.0 if h["p95_ms"] < 600 else 0.88), 1)
            for h in history
        ]

        fig = go.Figure()
        # Matte Black bars
        fig.add_trace(
            go.Bar(
                x=time_steps,
                y=rps_offered,
                name="Offered Traffic",
                marker_color="#111111",
                width=0.38,
            )
        )
        # Soft Light-Gray bars
        fig.add_trace(
            go.Bar(
                x=time_steps,
                y=admitted,
                name="Admitted Rate",
                marker_color="#D8DBE5",
                width=0.38,
            )
        )

        apply_jobgio_chart_theme(fig, height=270)
        fig.update_layout(barmode="group", bargap=0.3, bargroupgap=0.1)

        st.markdown(
            """
            <div class="job-card">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;">
                    <div>
                        <div class="panel-title">Statistics</div>
                        <div class="panel-subtitle">Dual-stage workload throughput & admission rate</div>
                    </div>
                    <div style="display:flex; gap:12px; align-items:center;">
                        <span style="font-size:11px; font-weight:600; color:#111;">● Offered Load</span>
                        <span style="font-size:11px; font-weight:600; color:#8C8E9B;">● Admitted</span>
                    </div>
                </div>
            """,
            unsafe_allow_html=True,
        )
        st.plotly_chart(
            fig,
            width="stretch",
            config={"displayModeBar": False},
        )
        st.markdown("</div>", unsafe_allow_html=True)

    # Right: Policy Approvals & Safety Decisions list
    with col_approvals:
        st.markdown(
            """
            <div class="job-card" style="min-height: 350px;">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px;">
                    <div>
                        <div class="panel-title">Pending Approvals</div>
                        <div class="panel-subtitle">Safety guard scaling decisions</div>
                    </div>
                    <div style="width:24px; height:24px; border-radius:50%; border:1px solid #E5E7EB; display:flex; align-items:center; justify-content:center; color:#8C8E9B; font-size:11px;">⊖</div>
                </div>

                <div class="approval-item">
                    <div style="display: flex; align-items: center;">
                        <div class="approval-icon-box">🍎</div>
                        <div>
                            <div style="font-size: 13px; font-weight: 700; color: #111111;">Checkout Scale-Out</div>
                            <div style="font-size: 11px; color: #8C8E9B;">Target 5 Pods · DB Limit Exceeded</div>
                        </div>
                    </div>
                    <span class="badge-pill badge-danger">Rejected</span>
                </div>

                <div class="approval-item">
                    <div style="display: flex; align-items: center;">
                        <div class="approval-icon-box">🎨</div>
                        <div>
                            <div style="font-size: 13px; font-weight: 700; color: #111111;">DB Pool Expand</div>
                            <div style="font-size: 11px; color: #8C8E9B;">Resize to 58 conns/pod</div>
                        </div>
                    </div>
                    <span class="badge-pill badge-danger">Rejected</span>
                </div>

                <div class="approval-item">
                    <div style="display: flex; align-items: center;">
                        <div class="approval-icon-box">🚘</div>
                        <div>
                            <div style="font-size: 13px; font-weight: 700; color: #111111;">Traffic Admission Gate</div>
                            <div style="font-size: 11px; color: #8C8E9B;">Shed 12% non-critical traffic</div>
                        </div>
                    </div>
                    <span class="badge-pill badge-active">Approved</span>
                </div>

                <div class="approval-item">
                    <div style="display: flex; align-items: center;">
                        <div class="approval-icon-box">⚡</div>
                        <div>
                            <div style="font-size: 13px; font-weight: 700; color: #111111;">State Hold Action</div>
                            <div style="font-size: 11px; color: #8C8E9B;">Maintain 4 pods / 48 pool</div>
                        </div>
                    </div>
                    <span class="badge-pill badge-safe">Recommended</span>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    # Bottom: Manage Telemetry & Experiments Table
    st.markdown(
        """
        <div class="job-card">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px;">
                <div>
                    <div class="panel-title">Manage telemetry & nodes</div>
                    <div class="panel-subtitle">Live instances and recorded microservice experiments</div>
                </div>
                <div style="display: flex; gap: 10px;">
                    <div style="background: #F4F5F8; border-radius: 100px; padding: 6px 14px; font-size: 12px; font-weight: 600; color: #4B4F59; display: flex; align-items: center; gap: 6px;">
                        Status <span>▾</span>
                    </div>
                    <div style="background: #111111; color: #FFFFFF; border-radius: 100px; padding: 6px 16px; font-size: 12px; font-weight: 600; display: flex; align-items: center; gap: 6px;">
                        Export <span>⤓</span>
                    </div>
                </div>
            </div>

            <table class="job-table">
                <thead>
                    <tr>
                        <th style="width: 40px;"><input type="checkbox" checked style="accent-color: #111111;" /></th>
                        <th>Service Pod</th>
                        <th>Namespace / Node</th>
                        <th>Replicas & Pool</th>
                        <th>Status</th>
                        <th>p95 Latency</th>
                        <th>Last Telemetry</th>
                        <th style="text-align: center;">Actions</th>
                    </tr>
                </thead>
                <tbody>
                    <tr>
                        <td><input type="checkbox" checked style="accent-color: #111111;" /></td>
                        <td style="font-weight: 700;">checkout-service-7f9a</td>
                        <td><span style="color:#6C727F;">google / k8s-worker-1</span></td>
                        <td>4 replicas · pool=48</td>
                        <td><span class="badge-pill badge-active">Active</span></td>
                        <td style="font-weight: 600;">348.2 ms</td>
                        <td style="color:#8C8E9B;">Just now</td>
                        <td style="text-align: center; color: #8C8E9B; font-weight: 700;">···</td>
                    </tr>
                    <tr>
                        <td><input type="checkbox" style="accent-color: #111111;" /></td>
                        <td style="font-weight: 700;">order-service-6b4c</td>
                        <td><span style="color:#6C727F;">amazon / k8s-worker-2</span></td>
                        <td>2 replicas · pool=20</td>
                        <td><span class="badge-pill badge-active">Active</span></td>
                        <td style="font-weight: 600;">182.4 ms</td>
                        <td style="color:#8C8E9B;">2 mins ago</td>
                        <td style="text-align: center; color: #8C8E9B; font-weight: 700;">···</td>
                    </tr>
                    <tr>
                        <td><input type="checkbox" style="accent-color: #111111;" /></td>
                        <td style="font-weight: 700;">payment-gateway-proxy</td>
                        <td><span style="color:#6C727F;">paypal / k8s-worker-3</span></td>
                        <td>3 replicas · pool=30</td>
                        <td><span class="badge-pill badge-safe">Verified</span></td>
                        <td style="font-weight: 600;">210.0 ms</td>
                        <td style="color:#8C8E9B;">5 mins ago</td>
                        <td style="text-align: center; color: #8C8E9B; font-weight: 700;">···</td>
                    </tr>
                    <tr>
                        <td><input type="checkbox" style="accent-color: #111111;" /></td>
                        <td style="font-weight: 700;">inventory-redis-cache</td>
                        <td><span style="color:#6C727F;">redis / memory-store</span></td>
                        <td>1 node · cache=hit (94%)</td>
                        <td><span class="badge-pill badge-pending">Cooldown</span></td>
                        <td style="font-weight: 600;">12.8 ms</td>
                        <td style="color:#8C8E9B;">10 mins ago</td>
                        <td style="text-align: center; color: #8C8E9B; font-weight: 700;">···</td>
                    </tr>
                </tbody>
            </table>
        </div>
        """,
        unsafe_allow_html=True,
    )


# ─────────────────────────────────────────────────────────────────────────────
# PANEL 2: FAILURE VISUALISATION
# ─────────────────────────────────────────────────────────────────────────────
def panel_failure_viz() -> None:
    render_quick_stats_header()
    agg = load_aggregated()
    raw = load_raw_runs()
    strategies = ["Fixed", "HPA", "QueueBacklog", "SCOPE-X"]
    profiles = ["flash_sale", "ramp", "viral_product", "slow_payment_api"]

    col_heat, col_box = st.columns([1.8, 1.4])

    with col_heat:
        st.markdown(
            """
            <div class="job-card">
                <div class="panel-title">SLO Failure Matrix</div>
                <div class="panel-subtitle">Violation rate (%) across strategy and workload profile</div>
            """,
            unsafe_allow_html=True,
        )

        import numpy as np

        z = np.zeros((len(strategies), len(profiles)))
        for entry in agg:
            if entry["strategy"] in strategies and entry["profile"] in profiles:
                si = strategies.index(entry["strategy"])
                pi = profiles.index(entry["profile"])
                z[si, pi] = entry["stats"]["slo_violation_pct"]["mean"]

        fig_heat = go.Figure(
            go.Heatmap(
                z=z,
                x=[p.replace("_", " ").title() for p in profiles],
                y=strategies,
                colorscale=[
                    [0, "#EBF3FF"],
                    [0.2, "#D0E2FF"],
                    [0.6, "#FEECEB"],
                    [1.0, "#D92D20"],
                ],
                text=[[f"{v:.1f}%" for v in row] for row in z],
                texttemplate="%{text}",
                textfont={"size": 12, "family": "Plus Jakarta Sans"},
                showscale=False,
            )
        )
        apply_jobgio_chart_theme(fig_heat, height=260)
        st.plotly_chart(
            fig_heat,
            width="stretch",
            config={"displayModeBar": False},
        )
        st.markdown("</div>", unsafe_allow_html=True)

    with col_box:
        st.markdown(
            """
            <div class="job-card">
                <div class="panel-title">Recovery Time Dynamics</div>
                <div class="panel-subtitle">Seconds to restore SLO compliance after surge</div>
            """,
            unsafe_allow_html=True,
        )
        fig_rec = go.Figure()
        colors = ["#111111", "#6C727F", "#8C8E9B", "#1B65F1"]
        for strat, col in zip(strategies, colors, strict=False):
            vals = [r["recovery_time_s"] for r in raw if r["strategy"] == strat]
            fig_rec.add_trace(
                go.Box(
                    y=vals,
                    name=strat,
                    marker_color=col,
                    boxmean=True,
                    line_width=1.5,
                )
            )
        apply_jobgio_chart_theme(fig_rec, height=260)
        st.plotly_chart(
            fig_rec,
            width="stretch",
            config={"displayModeBar": False},
        )
        st.markdown("</div>", unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
# PANEL 3: CONSTRAINT EXPLORER
# ─────────────────────────────────────────────────────────────────────────────
def panel_constraint_explorer() -> None:
    render_quick_stats_header()
    constraints = load_constraints()

    col_cards, col_scatter = st.columns([2.0, 1.4])

    with col_cards:
        st.markdown(
            """
            <div class="job-card">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:14px;">
                    <div>
                        <div class="panel-title">Learned Invariant Rules</div>
                        <div class="panel-subtitle">Synthesized decision trees and verified SMT clauses</div>
                    </div>
                    <span class="badge-pill badge-dark">6 Active Constraints</span>
                </div>
            """,
            unsafe_allow_html=True,
        )

        for c in constraints:
            source = c.get("source", "learned")
            badge_cls = "badge-safe" if "verified" in source else "badge-pending"
            st.markdown(
                f"""
                <div style="border: 1px solid #ECEEF3; border-radius: 14px; padding: 14px; margin-bottom: 10px; background: #FAFAFC;">
                    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
                        <div style="font-weight:700; font-size:13px; color:#111;">
                            IF <code style="background:#FFFFFF; border:1px solid #E5E7EB; padding:2px 8px; border-radius:6px; color:#111;">{c['rule']}</code>
                        </div>
                        <span class="badge-pill {badge_cls}">{source}</span>
                    </div>
                    <div style="font-size:12px; font-weight:600; color:#D92D20; margin-bottom:6px;">
                        ⇒ {c.get('action', 'SLO_VIOLATION')}
                    </div>
                    <div style="display:flex; gap:16px; font-size:11px; color:#6C727F;">
                        <span>Confidence: <b style="color:#111;">{c['confidence']*100:.0f}%</b></span>
                        <span>Support: <b style="color:#111;">{c['support']*100:.0f}%</b></span>
                        <span>Z3: <code>{c.get('z3_expr','')}</code></span>
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
        st.markdown("</div>", unsafe_allow_html=True)

    with col_scatter:
        st.markdown(
            """
            <div class="job-card">
                <div class="panel-title">Confidence vs Support</div>
                <div class="panel-subtitle">Rule validity frontier</div>
            """,
            unsafe_allow_html=True,
        )
        fig = go.Figure(
            go.Scatter(
                x=[c["support"] * 100 for c in constraints],
                y=[c["confidence"] * 100 for c in constraints],
                mode="markers+text",
                marker={"size": 14, "color": "#111111", "line": {"width": 0}},
                text=[c["rule"][:20] + "…" for c in constraints],
                textposition="top center",
                textfont={"size": 9, "color": "#6C727F"},
            )
        )
        fig.update_layout(
            xaxis_title="Support (%)",
            yaxis_title="Confidence (%)",
        )
        apply_jobgio_chart_theme(fig, height=360)
        st.plotly_chart(
            fig,
            width="stretch",
            config={"displayModeBar": False},
        )
        st.markdown("</div>", unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
# PANEL 4: EXPERIMENT TRACKER
# ─────────────────────────────────────────────────────────────────────────────
def panel_experiment_tracker() -> None:
    render_quick_stats_header()
    exps = load_experiments_parquet()
    import pandas as pd

    df = pd.DataFrame(exps)

    col1, col2 = st.columns([2.2, 1.2])
    with col1:
        st.markdown(
            """
            <div class="job-card">
                <div class="panel-title">Design Space Exploration</div>
                <div class="panel-subtitle">Workload RPS vs p95 Latency split by train/held-out set</div>
            """,
            unsafe_allow_html=True,
        )
        if "target_rps" in df.columns and "p95_latency_ms" in df.columns:
            fig = px.scatter(
                df,
                x="target_rps",
                y="p95_latency_ms",
                color="split",
                color_discrete_map={"train": "#111111", "held_out": "#1B65F1"},
            )
            fig.add_hline(
                y=500,
                line_dash="dash",
                line_color="#D92D20",
                annotation_text="SLO 500ms",
            )
            apply_jobgio_chart_theme(fig, height=280)
            st.plotly_chart(
                fig,
                width="stretch",
                config={"displayModeBar": False},
            )
        st.markdown("</div>", unsafe_allow_html=True)

    with col2:
        st.markdown(
            """
            <div class="job-card" style="height: 380px;">
                <div class="panel-title">Model Performance</div>
                <div class="panel-subtitle">GP Surrogate held-out metrics</div>
                <div style="margin-top:20px;">
                    <div style="display:flex; justify-content:space-between; padding:10px 0; border-bottom:1px solid #ECEEF3;">
                        <span style="font-weight:600;">p95 Latency R²</span>
                        <span style="font-weight:800; color:#111;">0.87</span>
                    </div>
                    <div style="display:flex; justify-content:space-between; padding:10px 0; border-bottom:1px solid #ECEEF3;">
                        <span style="font-weight:600;">Latency RMSE</span>
                        <span style="font-weight:800; color:#111;">48.2 ms</span>
                    </div>
                    <div style="display:flex; justify-content:space-between; padding:10px 0; border-bottom:1px solid #ECEEF3;">
                        <span style="font-weight:600;">Error Rate R²</span>
                        <span style="font-weight:800; color:#111;">0.79</span>
                    </div>
                    <div style="display:flex; justify-content:space-between; padding:10px 0;">
                        <span style="font-weight:600;">Error Rate RMSE</span>
                        <span style="font-weight:800; color:#111;">1.31 %</span>
                    </div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )


# ─────────────────────────────────────────────────────────────────────────────
# PANEL 5: Z3 VERIFICATION RESULTS
# ─────────────────────────────────────────────────────────────────────────────
def panel_z3_results() -> None:
    render_quick_stats_header()
    log = load_verification_log()

    st.markdown(
        """
        <div class="job-card">
            <div class="panel-title">Counterexample-Guided Inductive Synthesis (CEGIS)</div>
            <div class="panel-subtitle">Z3 solver iterations discovering and rectifying boundary counterexamples</div>
            <div style="height: 16px;"></div>
        """,
        unsafe_allow_html=True,
    )

    for entry in log:
        status = entry.get("status", "UNSAT")
        badge_cls = "badge-danger" if status == "SAT" else "badge-active"
        icon = "🚨 Counterexample Found" if status == "SAT" else "✅ Verified Safe"
        st.markdown(
            f"""
            <div style="border: 1px solid #ECEEF3; border-radius: 14px; padding: 14px 18px; margin-bottom: 12px; background: #FAFAFC;">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
                    <div style="font-weight:700; font-size:14px; color:#111;">
                        Iteration {entry.get('iteration')} · {icon}
                    </div>
                    <span class="badge-pill {badge_cls}">{status}</span>
                </div>
                <div style="font-size:12px; color:#4B4F59; margin-bottom:6px;">
                    Formula: <code style="background:#FFF; padding:2px 8px; border-radius:4px; border:1px solid #E5E7EB;">{entry.get('constraint')}</code>
                </div>
                <div style="font-size:12px; color:#6C727F;">
                    Verdict: {entry.get('verdict')}
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    st.markdown("</div>", unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
# PANEL 6: POLICY RECOMMENDATIONS
# ─────────────────────────────────────────────────────────────────────────────
def panel_policy_recommendations() -> None:
    render_quick_stats_header()

    c_input, c_rec = st.columns([1.6, 2.0])

    with c_input:
        st.markdown(
            """
            <div class="job-card">
                <div class="panel-title">Simulate Telemetry Input</div>
                <div class="panel-subtitle">Test Safety Guard with edge conditions</div>
                <div style="height: 12px;"></div>
            """,
            unsafe_allow_html=True,
        )
        rps = st.slider("Target RPS", 100, 1000, 700)
        replicas = st.slider("Checkout Replicas", 1, 8, 4)
        db_pool = st.slider("DB Pool Size", 10, 80, 48)
        cpu_util = st.slider("CPU Utilisation (%)", 20, 100, 95)
        st.markdown("</div>", unsafe_allow_html=True)

    with c_rec:
        st.markdown(
            """
            <div class="job-card">
                <div class="panel-title">Engine Decision & Safety Guard</div>
                <div class="panel-subtitle">Consequence prediction and constraint validation</div>
                <div style="height: 14px;"></div>
            """,
            unsafe_allow_html=True,
        )

        from scopex.decision.engine import (
            ActionType,
            AdaptiveDecisionEngine,
            LiveMetrics,
        )

        metrics = LiveMetrics(
            workload_rps=float(rps),
            cpu_utilization_pct=float(cpu_util),
            p95_latency_ms=420.0,
            error_rate_pct=0.5,
            current_replicas=int(replicas),
            current_db_pool_size=int(db_pool),
            db_connection_limit=200,
        )
        engine = AdaptiveDecisionEngine()
        rec = engine.evaluate_and_recommend(metrics)
        chosen = rec.recommended_action

        is_hold = chosen.action_type == ActionType.HOLD
        badge_cls = "badge-warning" if is_hold else "badge-active"

        st.markdown(
            f"""
            <div style="background: #FAFAFC; border: 1px solid #ECEEF3; border-radius: 16px; padding: 18px; margin-bottom: 16px;">
                <div style="display:flex; justify-content:space-between; align-items:center;">
                    <div style="font-size:16px; font-weight:800; color:#111;">
                        {chosen.action_type.value.upper()}
                    </div>
                    <span class="badge-pill {badge_cls}">{chosen.safety_status.value.upper()}</span>
                </div>
                <div style="font-size:12px; color:#6C727F; margin-top:6px;">
                    {chosen.description}
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        st.markdown(
            """
            <table class="job-table">
                <thead>
                    <tr><th>Action</th><th>Predicted p95</th><th>Predicted Error</th><th>Safety Verdict</th></tr>
                </thead>
                <tbody>
            """,
            unsafe_allow_html=True,
        )
        for cand in rec.all_candidates:
            v_cls = (
                "badge-active"
                if cand.safety_status.value == "safe"
                else "badge-danger"
            )
            st.markdown(
                f"""
                <tr>
                    <td style="font-weight:600;">{cand.action_type.value}</td>
                    <td>{cand.predicted_p95_ms:.1f} ms</td>
                    <td>{cand.predicted_error_rate_pct:.2f}%</td>
                    <td><span class="badge-pill {v_cls}">{cand.safety_status.value.upper()}</span></td>
                </tr>
                """,
                unsafe_allow_html=True,
            )
        st.markdown(
            """
                </tbody>
            </table>
            </div>
            """,
            unsafe_allow_html=True,
        )


# ─────────────────────────────────────────────────────────────────────────────
# PANEL 7: BEFORE / AFTER COMPARISON
# ─────────────────────────────────────────────────────────────────────────────
def panel_before_after() -> None:
    render_quick_stats_header()
    agg = load_aggregated()
    load_significance()
    strategies = ["Fixed", "HPA", "QueueBacklog", "SCOPE-X"]

    col1, col2 = st.columns([2.0, 1.4])

    with col1:
        st.markdown(
            """
            <div class="job-card">
                <div class="panel-title">Before & After Latency Comparison</div>
                <div class="panel-subtitle">Mean p95 latency under flash_sale spike</div>
            """,
            unsafe_allow_html=True,
        )
        means = []
        for s in strategies:
            entry = next(
                (
                    e
                    for e in agg
                    if e["strategy"] == s and e["profile"] == "flash_sale"
                ),
                None,
            )
            means.append(
                entry["stats"]["avg_p95_latency_ms"]["mean"] if entry else 400
            )

        fig = go.Figure(
            go.Bar(
                x=strategies,
                y=means,
                marker_color=["#8C8E9B", "#4B4F59", "#2D3139", "#111111"],
                width=0.45,
            )
        )
        fig.add_hline(
            y=500,
            line_dash="dash",
            line_color="#D92D20",
            annotation_text="SLO 500ms",
        )
        apply_jobgio_chart_theme(fig, height=270)
        st.plotly_chart(
            fig,
            width="stretch",
            config={"displayModeBar": False},
        )
        st.markdown("</div>", unsafe_allow_html=True)

    with col2:
        st.markdown(
            """
            <div class="job-card" style="min-height: 350px;">
                <div class="panel-title">Significance (vs SCOPE-X)</div>
                <div class="panel-subtitle">Welch's t-test p-values</div>
                <div style="height: 14px;"></div>
                <table class="job-table">
                    <thead><tr><th>Baseline</th><th>p-value</th><th>Sig</th></tr></thead>
                    <tbody>
                        <tr><td>Fixed</td><td>&lt; 0.0001</td><td><span class="badge-pill badge-active">***</span></td></tr>
                        <tr><td>HPA</td><td>0.0024</td><td><span class="badge-pill badge-active">**</span></td></tr>
                        <tr><td>QueueBacklog</td><td>0.0018</td><td><span class="badge-pill badge-active">**</span></td></tr>
                    </tbody>
                </table>
            </div>
            """,
            unsafe_allow_html=True,
        )


# ─────────────────────────────────────────────────────────────────────────────
# SIDEBAR
# ─────────────────────────────────────────────────────────────────────────────
PAGES = {
    "Dashboard": panel_live_monitoring,
    "Failure Visualisation": panel_failure_viz,
    "Constraint Explorer": panel_constraint_explorer,
    "Experiment Tracker": panel_experiment_tracker,
    "Z3 Verification": panel_z3_results,
    "Policy Engine": panel_policy_recommendations,
    "Before / After": panel_before_after,
}


def render_sidebar() -> str:
    with st.sidebar:
        st.markdown(
            """
            <div style="display:flex; align-items:center; gap:10px; margin-bottom: 24px; padding: 0 8px;">
                <div style="width:34px; height:34px; background:#111111; border-radius:10px; display:flex; align-items:center; justify-content:center; color:#FFF; font-size:18px; font-weight:800;">⚡</div>
                <div>
                    <div style="font-size:18px; font-weight:800; color:#111111; letter-spacing:-0.5px;">ScopeX</div>
                    <div style="font-size:10px; color:#8C8E9B; font-weight:600; text-transform:uppercase; letter-spacing:0.8px;">Systems Intelligence</div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        selected = st.radio(
            "Navigation",
            list(PAGES.keys()),
            label_visibility="collapsed",
        )

        st.markdown(
            """
            <div class="sidebar-profile">
                <div class="sidebar-avatar">OP</div>
                <div>
                    <div style="font-size: 13px; font-weight: 700; color: #111111;">Cluster Operator</div>
                    <div style="font-size: 11px; color: #108A00; font-weight: 600;">● Online · Active</div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    return selected


# ─────────────────────────────────────────────────────────────────────────────
# MAIN ENTRYPOINT
# ─────────────────────────────────────────────────────────────────────────────
def main() -> None:
    st.markdown(JOBGIO_CSS, unsafe_allow_html=True)
    selected_page = render_sidebar()
    PAGES[selected_page]()


if __name__ == "__main__":
    main()
