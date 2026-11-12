"""Tests for Task 14C: transfer of static-scaling constraints to HPA."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from scopex.discovery.constraints import Constraint
from scopex.discovery.hpa_transfer import (
    CLASS_DISAPPEARED,
    CLASS_SHIFTED,
    CLASS_UNCHANGED,
    CLASS_VIOLATED,
    CONNECTIONS_PER_REPLICA,
    BoundEstimate,
    ConstraintTransfer,
    HPAStudyDesign,
    PhaseMetrics,
    _classify,
    discover_transient_constraints,
    estimate_bound,
    evaluate_constraint,
    format_connection_report,
    format_transfer_table,
    load_validated_constraints,
    refit_shifted_bound,
    run_hpa_transfer_study,
    save_study_outputs,
    simulate_hpa_run,
    trace_connection_violations,
)


def _phase(
    phase: str,
    support: int,
    precision: float,
    recall: float = 1.0,
    n_rows: int = 100,
) -> PhaseMetrics:
    tp = int(round(support * precision))
    fp = support - tp
    fn = int(round(tp / recall - tp)) if recall > 0 else 0
    tn = n_rows - tp - fp - fn
    return PhaseMetrics(
        phase=phase,
        n_rows=n_rows,
        support=support,
        tp=tp,
        fp=fp,
        fn=max(0, fn),
        tn=max(0, tn),
        precision=precision,
        recall=recall,
        f1=0.0,
        accuracy=0.0,
        mean_p95_when_matched=0.0,
        mean_p95_when_unmatched=0.0,
    )


@pytest.fixture(scope="module")
def small_design() -> HPAStudyDesign:
    return HPAStudyDesign(
        cpu_targets=[50],
        max_replicas=[6],
        db_pools=[50],
        workloads=["flash_sale", "ramp"],
        repeats=1,
        heldout_fraction=0.5,
        duration=12,
        base_rps=400,
        warmup_seconds=2,
        settle_seconds=1,
        hpa_interval_seconds=2,
    )


# ---------------------------------------------------------------------------
# Design and split
# ---------------------------------------------------------------------------


def test_factorial_design_has_all_cells() -> None:
    design = HPAStudyDesign()
    cells = design.cells()
    assert len(cells) == 3 * 3 * 2 * 3
    assert {c["hpa_target_cpu"] for c in cells} == {50, 70, 90}
    assert {c["hpa_max_replicas"] for c in cells} == {4, 6, 10}
    assert {c["db_pool_size"] for c in cells} == {50, 100}
    assert {c["workload"] for c in cells} == {"ramp", "flash_sale", "sinusoidal"}


def test_heldout_split_is_disjoint_and_cell_level() -> None:
    design = HPAStudyDesign()
    train, heldout = design.split_cells()
    assert len(train) + len(heldout) == len(design.cells())
    assert len(heldout) == round(len(design.cells()) * design.heldout_fraction)

    train_keys = {tuple(sorted(cell.items())) for cell in train}
    heldout_keys = {tuple(sorted(cell.items())) for cell in heldout}
    assert train_keys.isdisjoint(heldout_keys)


# ---------------------------------------------------------------------------
# HPA simulation
# ---------------------------------------------------------------------------


def test_simulated_run_is_phase_labelled(small_design: HPAStudyDesign) -> None:
    cell = small_design.cells()[0]
    summary, frame = simulate_hpa_run(cell, small_design, repeat=0)

    assert len(frame) == small_design.duration
    assert set(frame["phase"]) <= {"steady", "transition"}
    assert (frame["phase"] == "steady").any()
    assert (frame["phase"] == "transition").any()
    # Warmup is always transition.
    assert (frame.loc[frame["second"] < small_design.warmup_seconds, "phase"] == "transition").all()
    assert np.allclose(
        frame["db_connection_demand"].to_numpy(),
        frame["checkout_replicas"].to_numpy() * CONNECTIONS_PER_REPLICA,
    )
    assert summary["steady_rows"] + summary["transition_rows"] == len(frame)


def test_hpa_scales_up_and_violates_connection_invariant() -> None:
    design = HPAStudyDesign(
        cpu_targets=[70],
        max_replicas=[10],
        db_pools=[50],
        workloads=["flash_sale"],
        repeats=1,
        duration=40,
        base_rps=500,
    )
    cell = design.cells()[0]
    summary, frame = simulate_hpa_run(cell, design, repeat=0)

    assert summary["peak_replicas"] == 10
    assert frame["connection_constraint_violated"].any()
    violating = frame[frame["connection_constraint_violated"]]
    assert (violating["checkout_replicas"] * CONNECTIONS_PER_REPLICA > 50).all()

    events = []
    previous = int(frame.iloc[0]["checkout_replicas"])
    for _, row in frame.iterrows():
        current = int(row["checkout_replicas"])
        if current != previous:
            events.append(
                {
                    "run_id": row["run_id"],
                    "timestamp_offset_seconds": int(row["second"]),
                    "event_type": "scale_up" if current > previous else "scale_down",
                    "from_replicas": previous,
                    "to_replicas": current,
                    "hpa_target_cpu": cell["hpa_target_cpu"],
                    "hpa_max_replicas": cell["hpa_max_replicas"],
                    "cpu_utilization_pct": row["cpu_utilization_pct"],
                }
            )
        previous = current

    traced = trace_connection_violations(frame, events)
    assert traced
    assert all(item["caused_by"] is not None for item in traced)
    assert any(item["caused_by"]["event_type"] == "scale_up" for item in traced)


# ---------------------------------------------------------------------------
# Constraint evaluation
# ---------------------------------------------------------------------------


def test_evaluate_constraint_separately_per_phase() -> None:
    n = 200
    rps = np.linspace(100, 600, n)
    phase = np.where(np.arange(n) % 2 == 0, "steady", "transition")
    p95 = np.where(rps > 480, 900.0, 100.0)
    df = pd.DataFrame(
        {
            "target_rps": rps,
            "checkout_replicas": 4,
            "db_pool_size": 100,
            "cache_enabled": True,
            "consumers": 2,
            "phase": phase,
            "p95_latency_ms": p95,
        }
    )
    constraint = Constraint(
        variables=["target_rps"],
        inequality="target_rps > 480",
        confidence=1.0,
        support=0.5,
        support_count=100,
        source_experiments=[],
        z3_expr="target_rps > 480",
        method="decision_tree",
    )

    steady = evaluate_constraint(constraint, df, "steady")
    transition = evaluate_constraint(constraint, df, "transition")
    overall = evaluate_constraint(constraint, df, "all")

    assert steady.phase == "steady"
    assert transition.phase == "transition"
    assert steady.n_rows + transition.n_rows == overall.n_rows
    for metrics in (steady, transition, overall):
        assert metrics.precision == pytest.approx(1.0)
        assert metrics.recall == pytest.approx(1.0)
        assert metrics.f1 == pytest.approx(1.0)
        assert metrics.support > 0


def test_estimate_bound_recovers_a_shifted_cut_point() -> None:
    rps = np.arange(0, 100, dtype=float)
    df = pd.DataFrame(
        {
            "target_rps": rps,
            "checkout_replicas": 4,
            "db_pool_size": 100,
            "cache_enabled": True,
            "consumers": 2,
            "p95_latency_ms": np.where(rps >= 60, 900.0, 100.0),
        }
    )
    constraint = Constraint(
        variables=["target_rps"],
        inequality="0.01 * target_rps >= 0.60",
        confidence=1.0,
        support=0.4,
        support_count=40,
        source_experiments=[],
        z3_expr="0.01 * target_rps >= 0.60",
        method="sparse_regression",
    )

    bound = estimate_bound(constraint, df, n_boot=50)
    assert bound.estimable
    assert bound.old_bound == pytest.approx(0.60)
    assert bound.new_bound == pytest.approx(0.60, abs=0.02)
    assert bound.ci_low <= bound.ci_high
    assert bound.f1 == pytest.approx(1.0)


def test_estimate_bound_marks_unsatisfied_context_as_not_estimable() -> None:
    df = pd.DataFrame(
        {
            "target_rps": [500.0] * 30,
            "cache_enabled": [True] * 30,
            "checkout_replicas": [2] * 30,
            "db_pool_size": [100] * 30,
            "consumers": [2] * 30,
            "p95_latency_ms": [100.0] * 30,
        }
    )
    constraint = Constraint(
        variables=["target_rps", "cache_enabled"],
        inequality="target_rps > 487.50 and cache_enabled <= 0.50",
        confidence=1.0,
        support=0.5,
        support_count=30,
        source_experiments=[],
        z3_expr="And(target_rps > 487.50, cache_enabled <= 0.50)",
        method="decision_tree",
    )
    bound = estimate_bound(constraint, df, n_boot=10)
    assert not bound.estimable
    assert "context" in bound.note


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def test_classification_unchanged() -> None:
    bound = BoundEstimate(
        statistic="score",
        comparator=">",
        old_bound=0.50,
        new_bound=0.505,
        ci_low=0.49,
        ci_high=0.52,
        f1=0.9,
        precision=0.9,
        recall=0.9,
        rows_used=100,
        estimable=True,
    )
    label, _ = _classify(
        source="learned",
        steady=_phase("steady", support=20, precision=0.9),
        transition=_phase("transition", support=20, precision=0.9),
        baseline_precision=0.9,
        bound=bound,
        connection_violations=0,
    )
    assert label == CLASS_UNCHANGED


def test_classification_shifted() -> None:
    bound = BoundEstimate(
        statistic="score",
        comparator=">",
        old_bound=0.50,
        new_bound=0.35,
        ci_low=0.34,
        ci_high=0.36,
        f1=0.9,
        precision=0.9,
        recall=0.9,
        rows_used=100,
        estimable=True,
    )
    label, evidence = _classify(
        source="learned",
        steady=_phase("steady", support=20, precision=0.9),
        transition=_phase("transition", support=20, precision=0.9),
        baseline_precision=0.9,
        bound=bound,
        connection_violations=0,
    )
    assert label == CLASS_SHIFTED
    assert "excludes old" in evidence


def test_classification_disappeared_and_violated() -> None:
    disappeared, _ = _classify(
        source="learned",
        steady=_phase("steady", support=0, precision=0.0),
        transition=_phase("transition", support=0, precision=0.0),
        baseline_precision=0.9,
        bound=None,
        connection_violations=0,
    )
    assert disappeared == CLASS_DISAPPEARED

    violated, evidence = _classify(
        source="structural",
        steady=_phase("steady", support=0, precision=0.0),
        transition=_phase("transition", support=0, precision=0.0),
        baseline_precision=None,
        bound=None,
        connection_violations=12,
    )
    assert violated == CLASS_VIOLATED
    assert "12 violating rows" in evidence


# ---------------------------------------------------------------------------
# Validated set loading and transient discovery
# ---------------------------------------------------------------------------


def test_load_validated_constraints_does_not_retrain() -> None:
    records = load_validated_constraints()
    assert len(records) == 11
    assert [record[0] for record in records] == [f"C{i:02d}" for i in range(1, 11)] + ["K01"]
    assert all(source == "learned" for _, source, _ in records[:-1])
    assert records[-1][1] == "structural"
    assert records[0][2].inequality.startswith("target_rps > 487.50")
    assert records[0][2].heldout_precision == pytest.approx(1.0)


def test_transient_rule_requires_transition_only_support() -> None:
    n = 400
    rng = np.random.default_rng(0)
    rps = rng.uniform(200, 600, n)
    replicas = rng.integers(1, 9, n)
    violated = (rps > 450) & (replicas <= 2)
    base = {
        "run_id": [f"run_{i}" for i in range(n)],
        "target_rps": rps,
        "checkout_replicas": replicas,
        "db_pool_size": np.full(n, 100),
        "cache_enabled": np.ones(n, dtype=bool),
        "consumers": np.full(n, 2),
        "workload_profile": ["flash_sale"] * n,
        "p95_latency_ms": np.where(violated, 1200.0, 90.0),
    }
    transition = pd.DataFrame({**base, "phase": ["transition"] * n})
    steady = pd.DataFrame(
        {
            **{**base, "p95_latency_ms": np.full(n, 90.0)},
            "phase": ["steady"] * n,
        }
    )
    train = pd.concat([transition.iloc[: n // 2], steady.iloc[: n // 2]], ignore_index=True)
    heldout = pd.concat([transition.iloc[n // 2 :], steady.iloc[n // 2 :]], ignore_index=True)
    found = discover_transient_constraints(train, heldout, validated=[])
    assert found
    assert any("checkout_replicas" in rule.inequality for rule in found)
    assert all((rule.heldout_precision or 0.0) >= 0.7 for rule in found)


# ---------------------------------------------------------------------------
# End-to-end orchestration, refit, and reporting
# ---------------------------------------------------------------------------


def test_small_study_end_to_end_and_outputs(small_design: HPAStudyDesign, tmp_path: Path) -> None:
    result = run_hpa_transfer_study(design=small_design, refit=False)

    assert len(result.transfers) == 11
    assert len(result.runs) == len(small_design.cells())
    assert len(result.rows) == len(small_design.cells()) * small_design.duration
    assert {t.classification for t in result.transfers} <= {
        CLASS_UNCHANGED,
        CLASS_SHIFTED,
        CLASS_DISAPPEARED,
        CLASS_VIOLATED,
    }

    table = format_transfer_table(result)
    header = table.splitlines()[0]
    assert "| ID |" in header and "Class" in header and "Old -> New bound" in header
    assert table.count("\n") + 1 >= 1 + len(result.transfers)
    assert "Connection invariant" in format_connection_report(result)

    paths = save_study_outputs(result, output_dir=tmp_path, prefix="hpa")
    payload = json.loads(paths["summary"].read_text())
    assert payload["task"] == "14C"
    assert len(payload["constraints"]) == 11
    assert payload["design"]["cells"] == len(small_design.cells())
    assert paths["runs"].exists() and paths["rows"].exists() and paths["table"].exists()


def test_refit_respects_budget(small_design: HPAStudyDesign) -> None:
    result = run_hpa_transfer_study(design=small_design, refit=False)
    steady_train = result.rows[
        (result.rows["split"] == "train") & (result.rows["phase"] == "steady")
    ]
    record = ConstraintTransfer(
        constraint_id="T01",
        source="learned",
        rule="0.001 * target_rps >= 0.40",
        variables=["target_rps"],
        classification=CLASS_SHIFTED,
        steady=_phase("steady", support=10, precision=0.8),
        transition=_phase("transition", support=10, precision=0.8),
        baseline_precision=0.8,
        baseline_recall=0.8,
        bound=BoundEstimate(
            statistic="score",
            comparator=">",
            old_bound=0.40,
            new_bound=0.35,
            ci_low=0.20,
            ci_high=0.50,
            f1=0.8,
            precision=0.8,
            recall=0.8,
            rows_used=len(steady_train),
            estimable=True,
        ),
        region={},
        evidence="synthetic shifted rule",
    )

    outcome = refit_shifted_bound(record, small_design, steady_train, budget=2, gp_iter=2)
    assert 0 <= outcome["experiments_used"] <= 2
    assert outcome["constraint_id"] == "T01"
    assert "bound_after" in outcome
