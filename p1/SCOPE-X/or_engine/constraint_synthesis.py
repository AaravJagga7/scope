"""Constraint synthesis: experimental observations -> interpretable rules.

A shallow decision tree is fitted to (features, violated?) where
violated = measured p95 latency > L_max. Every root-to-leaf path that ends
in a "violation" leaf becomes one conjunctive clause; the rule is the OR of
those clauses. Depth is capped so rules stay readable (<= 3 conditions per
clause by default).

Only *measured* experiments are used as training data - never GP
predictions - so a rule is always backed by testbed evidence.
"""
from __future__ import annotations

from typing import List, Optional, Sequence

import numpy as np
from sklearn.tree import DecisionTreeClassifier

from .rules import Atom, Rule, feature_matrix, simplify_clause

# Default features for the p95-latency constraint. Checkout only sees the
# admitted stream, so lambda and a enter through min(lambda, a) (total and
# per replica) rather than separately; this keeps rules short and stops the
# tree from splitting on traffic_rate where only admitted load matters.
# Pass ``features=rules.FEATURES`` to let it use the raw variables too.
LATENCY_FEATURES = ["replicas", "queue_capacity", "bucket_capacity", "admitted_load", "load_per_replica"]


def violation_labels(y_latency: np.ndarray, L_max: float) -> np.ndarray:
    return np.asarray(y_latency, float) > L_max


def _design(X: np.ndarray, features: Sequence[str]) -> np.ndarray:
    f = feature_matrix(X)
    return np.column_stack([f[n] for n in features])


def synthesize(X: np.ndarray, violated: np.ndarray, max_depth: int = 3,
               min_samples_leaf: int = 2, features: Sequence[str] = LATENCY_FEATURES,
               sample_weight: Optional[np.ndarray] = None, seed: int = 0) -> Rule:
    violated = np.asarray(violated, bool)
    if violated.all():
        return Rule([[]])                    # always violates (empty clause = True)
    if not violated.any():
        return Rule([])
    tree = DecisionTreeClassifier(max_depth=max_depth, min_samples_leaf=min_samples_leaf,
                                  class_weight="balanced", random_state=seed)
    tree.fit(_design(X, features), violated, sample_weight=sample_weight)
    t = tree.tree_
    clauses: List[List[Atom]] = []

    def walk(node: int, path: List[Atom]):
        if t.children_left[node] == -1:          # leaf
            if np.argmax(t.value[node][0]) == list(tree.classes_).index(True):
                clauses.append(simplify_clause(path))
            return
        f = features[t.feature[node]]
        thr = float(t.threshold[node])
        walk(t.children_left[node], path + [Atom(f, "<=", thr)])
        walk(t.children_right[node], path + [Atom(f, ">", thr)])

    walk(0, [])
    return merge_clauses(Rule(clauses))


def merge_clauses(rule: Rule) -> Rule:
    """Drop clauses subsumed by a more general clause (fewer, looser atoms)."""
    keep = []
    for i, c in enumerate(rule.clauses):
        subsumed = False
        for j, d in enumerate(rule.clauses):
            if i != j and _implies(c, d) and not (_implies(d, c) and j > i):
                subsumed = True
                break
        if not subsumed:
            keep.append(c)
    return Rule(keep, rule.target)


def _implies(c: List[Atom], d: List[Atom]) -> bool:
    """Syntactic check: every atom of d is implied by some atom of c."""
    for b in d:
        ok = any(a.feature == b.feature and a.op == b.op and
                 ((a.op == ">" and a.threshold >= b.threshold) or
                  (a.op == "<=" and a.threshold <= b.threshold)) for a in c)
        if not ok:
            return False
    return True


def synthesize_cv(X: np.ndarray, violated: np.ndarray, depths: Sequence[int] = (1, 2, 3),
                  folds: int = 5, sample_weight: Optional[np.ndarray] = None,
                  seed: int = 0, **kw) -> Rule:
    """Pick tree depth by stratified cross-validated F1 on the evidence,
    preferring the shallower (more interpretable) rule on near-ties."""
    from sklearn.model_selection import StratifiedKFold

    violated = np.asarray(violated, bool)
    n_min = min(violated.sum(), (~violated).sum())
    if n_min < 2:
        return synthesize(X, violated, max_depth=min(depths), sample_weight=sample_weight, seed=seed, **kw)
    k = int(min(folds, n_min))
    skf = StratifiedKFold(n_splits=k, shuffle=True, random_state=seed)
    scores = {}
    for d in depths:
        f1s = []
        for tr, te in skf.split(X, violated):
            sw = None if sample_weight is None else sample_weight[tr]
            r = synthesize(X[tr], violated[tr], max_depth=d, sample_weight=sw, seed=seed, **kw)
            p, t = r.predict(X[te]), violated[te]
            tp = (p & t).sum()
            f1s.append(2 * tp / max(p.sum() + t.sum(), 1))
        scores[d] = float(np.mean(f1s))
    best = max(scores.values())
    depth = min(d for d, s in scores.items() if s >= best - 0.01)
    rule = synthesize(X, violated, max_depth=depth, sample_weight=sample_weight, seed=seed, **kw)
    rule.cv = {"depth": depth, "cv_f1": scores}          # type: ignore[attr-defined]
    return rule


# --------------------------------------------------------------------------
# Monotone DNF synthesis
# --------------------------------------------------------------------------
# Direction in which each feature pushes towards violation (domain
# assumptions; the same ones z3_verification checks). A monotone rule only
# uses atoms in these directions, so it satisfies the monotonicity
# assumptions by construction, and its thresholds are exactly what
# near-boundary counterexamples are good at pinning down.
MONOTONE_DIRECTIONS = {
    "load_per_replica": ">",
    "admitted_load": ">",
    "bucket_capacity": ">",
    "replicas": "<=",
}


def _f1(pred: np.ndarray, t: np.ndarray, w: np.ndarray) -> float:
    tp = (w * (pred & t)).sum()
    return float(2 * tp / max((w * pred).sum() + (w * t).sum(), 1e-12))


def synthesize_monotone(X: np.ndarray, violated: np.ndarray,
                        directions: Optional[dict] = None, max_clauses: int = 3,
                        max_atoms: int = 2, n_thresholds: int = 40,
                        min_gain: float = 0.005, sample_weight: Optional[np.ndarray] = None,
                        **_) -> Rule:
    """Greedy weighted-F1 search over monotone DNF rules.

    Each clause is a conjunction of <= ``max_atoms`` atoms; clauses are
    added greedily while they raise F1 on the evidence by >= ``min_gain``,
    then every threshold is refined by coordinate search.
    """
    directions = directions or MONOTONE_DIRECTIONS
    t = np.asarray(violated, bool)
    w = np.ones(len(t)) if sample_weight is None else np.asarray(sample_weight, float)
    if not t.any():
        return Rule([])
    feats = feature_matrix(X)

    atoms: List[Atom] = []
    for f, op in directions.items():
        v = np.unique(feats[f])
        if len(v) < 2:
            continue
        mids = (v[:-1] + v[1:]) / 2
        if len(mids) > n_thresholds:
            mids = np.unique(np.quantile(mids, np.linspace(0, 1, n_thresholds)))
        atoms += [Atom(f, op, float(m)) for m in mids]
    A = np.column_stack([a.eval(feats) for a in atoms])          # n x m

    # candidate clauses: singles + pairs on different features
    cl_idx = [(i,) for i in range(len(atoms))]
    if max_atoms >= 2:
        for i in range(len(atoms)):
            for j in range(i + 1, len(atoms)):
                if atoms[i].feature != atoms[j].feature:
                    cl_idx.append((i, j))

    def clause_mask(ix):
        m = A[:, ix[0]].copy()
        for k in ix[1:]:
            m &= A[:, k]
        return m

    chosen: List[tuple] = []
    current = np.zeros(len(t), bool)
    best_f1 = 0.0
    for _ in range(max_clauses):
        best = None
        for ix in cl_idx:
            f1 = _f1(current | clause_mask(ix), t, w)
            if best is None or f1 > best[0] + 1e-12 or (abs(f1 - best[0]) <= 1e-12 and len(ix) < len(best[1])):
                best = (f1, ix)
        if best is None or best[0] < best_f1 + min_gain:
            break
        best_f1 = best[0]
        chosen.append(best[1])
        current |= clause_mask(best[1])

    clauses = [[atoms[k] for k in ix] for ix in chosen]
    rule = Rule(clauses)

    # coordinate refinement of thresholds
    for _ in range(2):
        for ci, c in enumerate(rule.clauses):
            for aj, a in enumerate(c):
                vals = np.unique([b.threshold for b in atoms if b.feature == a.feature])
                best_t, best_s = a.threshold, _f1(rule.predict(X), t, w)
                for v in vals:
                    c[aj] = Atom(a.feature, a.op, float(v))
                    s = _f1(rule.predict(X), t, w)
                    if s > best_s + 1e-12:
                        best_t, best_s = float(v), s
                c[aj] = Atom(a.feature, a.op, best_t)
    return merge_clauses(Rule([simplify_clause(c) for c in rule.clauses]))
