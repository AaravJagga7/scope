"""Structured rule format for operational constraints.

A candidate constraint is a DNF formula over threshold atoms:

    VIOLATION  <=  (atom AND atom ...) OR (atom AND ...) OR ...

Each atom is ``feature op threshold`` with op in {'>', '<='}. Features are
the raw experiment variables plus two derived ones:

    admitted_load    = min(traffic_rate, admission_rate)
                       the rate the shield can push into Checkout as a whole
                       (and so into the shared database)
    load_per_replica = admitted_load / replicas
                       the rate each Checkout replica has to absorb
 Because ``replicas`` is a positive integer, an atom on it stays in
linear arithmetic:  min(lambda, a)/r > t  <=>  lambda > t*r  AND  a > t*r.
That keeps every rule inside the fragment Z3 decides (linear integer/real
arithmetic) and inside the exact fallback solver.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Dict, List, Sequence

import numpy as np

RAW = ["traffic_rate", "replicas", "admission_rate", "queue_capacity", "bucket_capacity"]
DERIVED = ["admitted_load", "load_per_replica"]
FEATURES = RAW + DERIVED
UNITS = {"traffic_rate": "req/s", "admission_rate": "req/s", "admitted_load": "req/s",
         "load_per_replica": "req/s per replica",
         "replicas": "", "queue_capacity": "", "bucket_capacity": ""}


def feature_matrix(X: np.ndarray) -> Dict[str, np.ndarray]:
    """X columns follow RAW order. Works on any leading shape."""
    X = np.asarray(X, float)
    f = {n: X[..., i] for i, n in enumerate(RAW)}
    f["admitted_load"] = np.minimum(f["traffic_rate"], f["admission_rate"])
    f["load_per_replica"] = f["admitted_load"] / f["replicas"]
    return f


@dataclass(frozen=True)
class Atom:
    feature: str
    op: str            # '>' or '<='
    threshold: float

    def __post_init__(self):
        if self.feature not in FEATURES:
            raise ValueError(f"unknown feature {self.feature}")
        if self.op not in (">", "<="):
            raise ValueError("op must be '>' or '<='")

    def eval(self, feats: Dict[str, np.ndarray]) -> np.ndarray:
        v = feats[self.feature]
        return v > self.threshold if self.op == ">" else v <= self.threshold

    def negate(self) -> "Atom":
        return Atom(self.feature, "<=" if self.op == ">" else ">", self.threshold)

    def text(self) -> str:
        t = self.threshold
        ts = f"{t:.0f}" if abs(t) >= 10 else f"{t:.2f}"
        name = {"load_per_replica": "min(λ,a)/r", "admitted_load": "min(λ,a)"}.get(self.feature, self.feature)
        return f"{name} {self.op} {ts}"

    def to_dict(self) -> dict:
        return {"feature": self.feature, "op": self.op, "threshold": round(float(self.threshold), 4)}


@dataclass
class Rule:
    """VIOLATION if any clause holds; a clause is a conjunction of atoms."""
    clauses: List[List[Atom]] = field(default_factory=list)
    target: str = "L95_ms > L_max"

    def predict(self, X: np.ndarray) -> np.ndarray:
        feats = feature_matrix(X)
        shape = np.asarray(X).shape[:-1]
        out = np.zeros(shape, bool)
        for c in self.clauses:
            m = np.ones(shape, bool)
            for a in c:
                m &= a.eval(feats)
            out |= m
        return out

    def atoms(self) -> List[Atom]:
        return [a for c in self.clauses for a in c]

    def text(self) -> str:
        if not self.clauses:
            return "never violates (empty rule)"
        body = "\n  OR ".join("(" + " AND ".join(a.text() for a in c) + ")" for c in self.clauses)
        return f"IF {body}\nTHEN {self.target}"

    def to_dict(self) -> dict:
        return {"target": self.target, "clauses": [[a.to_dict() for a in c] for c in self.clauses]}

    @staticmethod
    def from_dict(d: dict) -> "Rule":
        return Rule([[Atom(**a) for a in c] for c in d["clauses"]], d.get("target", "L95_ms > L_max"))

    def size(self) -> int:
        return sum(len(c) for c in self.clauses)


def simplify_clause(atoms: Sequence[Atom]) -> List[Atom]:
    """Keep only the tightest bound per (feature, op)."""
    best: Dict[tuple, Atom] = {}
    for a in atoms:
        k = (a.feature, a.op)
        if k not in best:
            best[k] = a
        elif a.op == ">" and a.threshold > best[k].threshold:
            best[k] = a
        elif a.op == "<=" and a.threshold < best[k].threshold:
            best[k] = a
    return sorted(best.values(), key=lambda a: (FEATURES.index(a.feature), a.op))
