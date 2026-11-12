"""Constraint-discovery metrics: precision / recall / F1 of a rule against
held-out ground truth, plus refinement bookkeeping."""
from __future__ import annotations

from typing import Dict

import numpy as np

from or_engine.rules import RAW, Rule

from .prediction_metrics import classification_report


def rule_report(rule: Rule, X: np.ndarray, latency: np.ndarray, L_max: float) -> Dict[str, float]:
    """Positive class = SLO violation (latency > L_max)."""
    return classification_report(np.asarray(latency) > L_max, rule.predict(X))


def refinement_report(cegis) -> Dict[str, object]:
    h = cegis.history
    return {
        "iterations": len(h),
        "converged": bool(h and h[-1].converged),
        "extra_experiments": cegis.extra_experiments,
        "counterexamples_found": int(sum(i.counterexamples for i in h)),
        "counterexamples_per_iteration": [i.counterexamples for i in h],
        "tested_per_iteration": [i.tested for i in h],
        "by_source": _merge_sources(h),
        "assumption_violations": sorted({a for i in h for a in i.assumption_violations}),
        "pruned_clauses": int(sum(i.pruned_clauses for i in h)),
        "checks_sat": int(sum(sum(c.sat for c in i.checks) for i in h)),
        "checks_unsat": int(sum(sum(not c.sat for c in i.checks) for i in h)),
        "unresolved_validation_failures": cegis.unresolved(),
    }


def _merge_sources(history) -> Dict[str, Dict[str, int]]:
    out: Dict[str, Dict[str, int]] = {}
    for it in history:
        for src, (t, k) in it.by_source.items():
            d = out.setdefault(src, {"tested": 0, "counterexamples": 0})
            d["tested"] += t
            d["counterexamples"] += k
    return out
