"""Tests for the Task 18 full evaluation pipeline.

Verifies:
- Evaluation runs correctly (quick mode: 3 reps, flash_sale only).
- Artefacts are produced in the expected structure.
- Statistical aggregation is correct.
- Significance tests are computed.
- All result keys are present in the JSON outputs.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# ---------------------------------------------------------------------------
# Import the evaluation module (adjust RESULTS_DIR to a temp dir)
# ---------------------------------------------------------------------------


def _run_eval_in_tmpdir(tmp_path: Path, n_reps: int = 3, profiles: list[str] | None = None):
    """Run the evaluation pipeline with RESULTS_DIR pointing to tmp_path."""
    import scripts.run_evaluation as ev

    # Patch the output directories to use tmp_path
    orig_results = ev.RESULTS_DIR
    orig_figures = ev.FIGURES_DIR
    orig_tables = ev.TABLES_DIR

    ev.RESULTS_DIR = tmp_path
    ev.FIGURES_DIR = tmp_path / "figures"
    ev.TABLES_DIR = tmp_path / "tables"

    try:
        ev.run_evaluation(n_reps=n_reps, profiles=profiles or ["flash_sale"])
    finally:
        ev.RESULTS_DIR = orig_results
        ev.FIGURES_DIR = orig_figures
        ev.TABLES_DIR = orig_tables

    return tmp_path


# ===========================================================================
# TestStatisticsHelpers
# ===========================================================================


class TestStatisticsHelpers:
    """Unit tests for statistical helper functions."""

    def test_ci95_single_value(self):
        import scripts.run_evaluation as ev

        lo, hi = ev._ci95([42.0])
        assert lo == hi == 42.0

    def test_ci95_known_distribution(self):
        import scripts.run_evaluation as ev

        vals = [100.0] * 10  # zero variance
        lo, hi = ev._ci95(vals)
        assert lo == pytest.approx(100.0, abs=1e-6)
        assert hi == pytest.approx(100.0, abs=1e-6)

    def test_ci95_width_increases_with_std(self):
        import scripts.run_evaluation as ev

        narrow = ev._ci95([50.0, 51.0, 49.0, 50.5])
        wide   = ev._ci95([10.0, 90.0, 5.0, 95.0])
        assert (wide[1] - wide[0]) > (narrow[1] - narrow[0])

    def test_aggregate_metrics_all_present(self):
        import scripts.run_evaluation as ev

        runs = [
            ev.RunResult(
                strategy="SCOPE-X", profile="flash_sale", seed=42 + i,
                avg_p95_latency_ms=350.0 + i,
                max_p95_latency_ms=480.0 + i,
                avg_error_rate_pct=0.3,
                slo_violation_pct=0.5,
                hourly_cost_runrate=0.34,
                admission_rate_pct=98.0,
                avg_replicas=3.2,
                total_cost_usd=0.001,
                replica_minutes=6.4,
                slo_violation_duration_s=1.0,
                recovery_time_s=2.0,
            )
            for i in range(5)
        ]
        agg = ev._aggregate(runs, "SCOPE-X", "flash_sale")
        assert agg.n_reps == 5
        assert "avg_p95_latency_ms" in agg.stats
        assert "mean" in agg.stats["avg_p95_latency_ms"]
        assert "std" in agg.stats["avg_p95_latency_ms"]
        assert "ci_lo" in agg.stats["avg_p95_latency_ms"]
        assert "ci_hi" in agg.stats["avg_p95_latency_ms"]

    def test_aggregate_mean_correct(self):
        import scripts.run_evaluation as ev

        vals = [100.0, 200.0, 300.0]
        runs = [
            ev.RunResult(
                strategy="Fixed", profile="ramp", seed=42 + i,
                avg_p95_latency_ms=v,
                max_p95_latency_ms=v + 50,
                avg_error_rate_pct=0.0,
                slo_violation_pct=0.0,
                hourly_cost_runrate=0.28,
                admission_rate_pct=100.0,
                avg_replicas=2.0,
                total_cost_usd=0.001,
            )
            for i, v in enumerate(vals)
        ]
        agg = ev._aggregate(runs, "Fixed", "ramp")
        assert agg.stats["avg_p95_latency_ms"]["mean"] == pytest.approx(200.0, abs=0.01)

    def test_significance_test_returns_all_strategies(self):
        import scripts.run_evaluation as ev

        runs = []
        for strat in ev.STRATEGIES:
            for i in range(5):
                runs.append(ev.RunResult(
                    strategy=strat, profile="flash_sale", seed=42 + i,
                    avg_p95_latency_ms=350.0 if strat == "SCOPE-X" else 600.0,
                    max_p95_latency_ms=480.0,
                    avg_error_rate_pct=0.3,
                    slo_violation_pct=1.0 if strat == "SCOPE-X" else 50.0,
                    hourly_cost_runrate=0.34,
                    admission_rate_pct=98.0,
                    avg_replicas=3.0,
                    total_cost_usd=0.001,
                ))
        sig = ev.run_significance_tests(runs, "flash_sale", baseline="SCOPE-X")
        # All non-SCOPE-X strategies should appear
        for strat in ["Fixed", "HPA", "QueueBacklog"]:
            assert strat in sig, f"{strat} missing from significance tests"

    def test_strategy_shortname_mapping(self):
        import scripts.run_evaluation as ev

        assert ev._strategy_shortname("FixedReplicas") == "Fixed"
        assert ev._strategy_shortname("KubernetesHPA") == "HPA"
        assert ev._strategy_shortname("QueueBacklog-v2") == "QueueBacklog"
        assert ev._strategy_shortname("SCOPE-X Strategy") == "SCOPE-X"

    def test_compute_recovery_time_no_violations(self):
        import scripts.run_evaluation as ev
        from scopex.decision.harness import StrategyComparisonRecord, TimeStepTelemetry

        # Build a mock record with no violations
        tele = [
            TimeStepTelemetry(
                time_step=t, workload_rps=300, admitted_rps=300, shed_rps=0,
                replicas=2, cpu_limit_milli=1000, db_pool_size=20, cache_enabled=True,
                cpu_utilization_pct=40, queue_backlog=0, p95_latency_ms=120,
                error_rate_pct=0.1, total_db_connections=40, step_cost=0.0001,
                is_slo_violated=False,
            )
            for t in range(30)
        ]
        rec = StrategyComparisonRecord(
            strategy_name="SCOPE-X", description="test",
            total_requests=9000, admitted_requests=9000, shed_requests=0,
            admission_rate_pct=100.0, avg_p95_latency_ms=120.0, max_p95_latency_ms=180.0,
            avg_error_rate_pct=0.1, max_error_rate_pct=0.2, slo_violation_steps=0,
            total_steps=30, slo_violation_pct=0.0, avg_replicas=2.0, max_replicas=2,
            total_cost_usd=0.003, hourly_cost_runrate=0.36, status="PASSED",
            status_rationale="OK", telemetry=tele,
        )
        assert ev._compute_recovery_time(rec) == 0.0


# ===========================================================================
# TestEvaluationPipeline (integration)
# ===========================================================================


class TestEvaluationPipeline:
    """Integration tests: run a quick eval and verify artefacts."""

    @pytest.fixture(scope="class")
    def eval_output(self, tmp_path_factory):
        """Run evaluation once and return the output directory."""
        tmp = tmp_path_factory.mktemp("eval_out")
        _run_eval_in_tmpdir(tmp, n_reps=3, profiles=["flash_sale"])
        return tmp

    def test_raw_runs_json_exists(self, eval_output):
        assert (eval_output / "raw_runs.json").exists()

    def test_aggregated_stats_json_exists(self, eval_output):
        assert (eval_output / "aggregated_stats.json").exists()

    def test_significance_tests_json_exists(self, eval_output):
        assert (eval_output / "significance_tests.json").exists()

    def test_findings_summary_exists(self, eval_output):
        assert (eval_output / "findings_summary.md").exists()
        content = (eval_output / "findings_summary.md").read_text()
        assert "SCOPE-X" in content
        assert "Finding 1" in content

    def test_all_four_strategy_figures_generated(self, eval_output):
        for fig in ["comparison_flash_sale.png", "timeseries_flash_sale.png",
                    "radar_flash_sale.png", "pareto_flash_sale.png",
                    "recovery_flash_sale.png", "heatmap_slo_violations.png"]:
            path = eval_output / "figures" / fig
            assert path.exists(), f"Missing figure: {fig}"

    def test_latex_and_csv_tables_generated(self, eval_output):
        for f in ["comparison_flash_sale.tex", "comparison_flash_sale.csv"]:
            assert (eval_output / "tables" / f).exists(), f"Missing table: {f}"

    def test_raw_runs_has_all_strategies(self, eval_output):
        data = json.loads((eval_output / "raw_runs.json").read_text())
        strategies = {r["strategy"] for r in data}
        assert strategies == {"Fixed", "HPA", "QueueBacklog", "SCOPE-X"}

    def test_raw_runs_correct_count(self, eval_output):
        data = json.loads((eval_output / "raw_runs.json").read_text())
        # 3 reps × 1 profile × 4 strategies = 12
        assert len(data) == 12

    def test_aggregated_stats_has_expected_metrics(self, eval_output):
        data = json.loads((eval_output / "aggregated_stats.json").read_text())
        for entry in data:
            for metric in ["avg_p95_latency_ms", "slo_violation_pct",
                           "hourly_cost_runrate", "avg_error_rate_pct"]:
                assert metric in entry["stats"], f"Missing metric {metric} in {entry['strategy']}"
                assert "mean" in entry["stats"][metric]
                assert "std" in entry["stats"][metric]
                assert "ci_lo" in entry["stats"][metric]
                assert "ci_hi" in entry["stats"][metric]

    def test_scopex_slo_violations_below_10pct(self, eval_output):
        """SCOPE-X should achieve < 10% SLO violations on flash_sale."""
        data = json.loads((eval_output / "aggregated_stats.json").read_text())
        scopex_entry = next(
            (e for e in data if e["strategy"] == "SCOPE-X" and e["profile"] == "flash_sale"),
            None,
        )
        assert scopex_entry is not None
        mean_viol = scopex_entry["stats"]["slo_violation_pct"]["mean"]
        assert mean_viol < 10.0, (
            f"SCOPE-X SLO violation mean {mean_viol:.1f}% should be < 10% on flash_sale"
        )

    def test_fixed_slo_violations_above_scopex(self, eval_output):
        """Fixed strategy should have higher SLO violations than SCOPE-X."""
        data = json.loads((eval_output / "aggregated_stats.json").read_text())
        fixed = next(e for e in data if e["strategy"] == "Fixed" and e["profile"] == "flash_sale")
        scopex = next(e for e in data if e["strategy"] == "SCOPE-X" and e["profile"] == "flash_sale")
        assert (
            fixed["stats"]["slo_violation_pct"]["mean"]
            > scopex["stats"]["slo_violation_pct"]["mean"]
        )

    def test_significance_tests_structure(self, eval_output):
        data = json.loads((eval_output / "significance_tests.json").read_text())
        assert "flash_sale" in data
        for strat in ["Fixed", "HPA", "QueueBacklog"]:
            assert strat in data["flash_sale"], f"Missing {strat} in sig tests"
        # Each entry should have metric-level results
        for strat_data in data["flash_sale"].values():
            for m_data in strat_data.values():
                assert "t_stat" in m_data
                assert "p_value" in m_data
                assert "significance" in m_data

    def test_csv_table_parseable(self, eval_output):
        import csv
        import io

        content = (eval_output / "tables" / "comparison_flash_sale.csv").read_text()
        reader = csv.DictReader(io.StringIO(content))
        rows = list(reader)
        assert len(rows) > 0
        assert "metric" in rows[0] or "avg_p95_latency_ms" in str(rows)

    def test_latex_table_has_booktabs_commands(self, eval_output):
        content = (eval_output / "tables" / "comparison_flash_sale.tex").read_text()
        assert r"\toprule" in content
        assert r"\midrule" in content
        assert r"\bottomrule" in content
        assert "SCOPE-X" in content

    def test_figure_files_nonzero_size(self, eval_output):
        """All generated PNG files should be non-trivially large (>5KB)."""
        for png in (eval_output / "figures").glob("*.png"):
            size = png.stat().st_size
            assert size > 5000, f"Figure {png.name} is suspiciously small: {size} bytes"


# ===========================================================================
# TestTableRenderers
# ===========================================================================


class TestTableRenderers:
    """Unit tests for table rendering functions."""

    def _make_agg(self, strategy: str, profile: str = "flash_sale") -> Any:
        import scripts.run_evaluation as ev

        metrics_extended = ev.METRICS + ["replica_minutes", "slo_violation_duration_s", "recovery_time_s"]
        stats = {
            m: {"mean": 100.0, "std": 5.0, "ci_lo": 95.0, "ci_hi": 105.0}
            for m in metrics_extended
        }
        return ev.AggResult(strategy=strategy, profile=profile, n_reps=5, stats=stats)

    def test_render_latex_table_outputs_string(self):
        import scripts.run_evaluation as ev

        agg_list = [self._make_agg(s) for s in ev.STRATEGIES]
        latex = ev.render_latex_table(agg_list, "flash_sale", {})
        assert isinstance(latex, str)
        assert r"\begin{table}" in latex
        assert r"\end{table}" in latex

    def test_render_csv_table_has_header(self):
        import scripts.run_evaluation as ev

        agg_list = [self._make_agg(s) for s in ev.STRATEGIES]
        csv_str = ev.render_csv_table(agg_list, "flash_sale")
        lines = csv_str.strip().split("\n")
        header = lines[0]
        assert "metric" in header
        assert "SCOPE-X_mean" in header
