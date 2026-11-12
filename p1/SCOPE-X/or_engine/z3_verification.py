"""Formal checks on candidate constraints (Z3, with an exact fallback).

What is checked
---------------
Z3 reasons about the *encoded model*: the bounded experimental domain plus
the candidate rule plus stated domain assumptions. It does not test the
e-commerce system. Each check returns SAT (with a witness configuration)
or UNSAT (no such configuration exists under the encoding):

1. ``clause_satisfiable``   - is each clause reachable inside the domain?
                              UNSAT => dead clause, drop it.
2. ``nonvacuous``           - does the rule allow both outcomes? (R and not-R SAT)
3. ``monotone``             - domain assumption, e.g. "adding replicas never
                              *creates* a violation" or "more traffic never
                              *removes* one". SAT => the rule contradicts the
                              assumption; the witness pair is a candidate
                              counterexample to run on the testbed.
4. ``boundary_probe``       - a configuration within delta of one atom's
                              threshold, on the side the rule calls safe
                              (or violating). Probes whether the threshold
                              sits in the right place.

SAT witnesses are *candidates*; CEGIS validates them on the testbed.

Backends
--------
``z3``     the Z3 SMT solver (``pip install z3-solver``) - linear integer /
           real arithmetic over the domain box.
``exact``  a decision procedure for exactly this rule fragment. Every atom
           (including the derived load atoms: min(lambda, a) > t splits into
           bounds on lambda and a, and replicas is an integer)
           is an axis-aligned threshold once replicas is fixed, so truth is
           constant between consecutive critical values. Evaluating one
           representative point per interval of the induced grid therefore
           decides every query exactly. Used automatically when z3 is not
           installed; ``tests/test_constraints.py`` cross-checks the two
           backends when both are available.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

import numpy as np

from experiment_engine.experiment_config import DEFAULT_SPACE, SearchSpace

from .rules import RAW, Atom, Rule, feature_matrix

try:                                     # pragma: no cover - depends on env
    import z3  # type: ignore
    HAVE_Z3 = True
except Exception:                        # pragma: no cover
    z3 = None
    HAVE_Z3 = False


@dataclass
class CheckResult:
    check: str
    status: str                          # "sat" | "unsat"
    witness: List[np.ndarray] = field(default_factory=list)   # 1 or 2 configs
    backend: str = ""
    detail: str = ""

    @property
    def sat(self) -> bool:
        return self.status == "sat"

    def to_dict(self) -> dict:
        return {"check": self.check, "status": self.status, "backend": self.backend,
                "detail": self.detail,
                "witness": [dict(zip(RAW, map(lambda v: round(float(v), 3), w))) for w in self.witness]}


# Domain assumptions encoded for the monotonicity checks.
# (variable, kind): kind "never_creates" = increasing var never turns a safe
# config into a violating one; "never_removes" = increasing var never turns
# a violating config into a safe one.
DEFAULT_ASSUMPTIONS = [
    ("replicas", "never_creates"),
    ("traffic_rate", "never_removes"),
    ("admission_rate", "never_removes"),
    ("bucket_capacity", "never_removes"),
]


# =========================================================== exact backend
class ExactBackend:
    name = "exact"

    def __init__(self, space: SearchSpace = DEFAULT_SPACE, seed: int = 0):
        self.space = space
        self.rng = np.random.default_rng(seed)
        self.vars = {v.name: v for v in space.variables}
        self.r_values = np.arange(int(self.vars["replicas"].low), int(self.vars["replicas"].high) + 1)

    # ---- grid construction ------------------------------------------------
    def _critical(self, atoms: Sequence[Atom], deltas: Dict[str, float]) -> Dict[str, list]:
        crit = {n: [self.vars[n].low, self.vars[n].high] for n in RAW}
        for a in atoms:
            ts = [a.threshold] + ([a.threshold - deltas.get(a.feature, 0), a.threshold + deltas.get(a.feature, 0)]
                                  if deltas.get(a.feature) else [])
            if a.feature == "load_per_replica":
                for t in ts:
                    for r in self.r_values:
                        crit["traffic_rate"].append(t * r)
                        crit["admission_rate"].append(t * r)
            elif a.feature == "admitted_load":
                for t in ts:
                    crit["traffic_rate"].append(t)
                    crit["admission_rate"].append(t)
            else:
                crit[a.feature].extend(ts)
        return crit

    def _reps(self, name: str, values: list) -> np.ndarray:
        v = self.vars[name]
        if name == "replicas":
            return self.r_values.astype(float)
        c = np.unique(np.clip(np.asarray(values, float), v.low, v.high))
        if v.integer:
            cand = set()
            for x in c:
                for y in (np.floor(x), np.ceil(x), np.floor(x) + 1, np.ceil(x) - 1):
                    if v.low <= y <= v.high:
                        cand.add(float(y))
            mids = (c[:-1] + c[1:]) / 2
            for m in mids:
                cand.add(float(np.clip(np.round(m), v.low, v.high)))
            return np.array(sorted(cand))
        mids = (c[:-1] + c[1:]) / 2
        return np.unique(np.concatenate([c, mids]))

    def _axes(self, atoms, deltas) -> List[np.ndarray]:
        crit = self._critical(atoms, deltas)
        return [self._reps(n, crit[n]) for n in RAW]

    def _masks(self, axes, fn) -> np.ndarray:
        """Evaluate ``fn(X_slice) -> bool`` over the grid one replica value
        at a time (keeps memory bounded) and stack into the full tensor."""
        ri = RAW.index("replicas")
        others = [a for i, a in enumerate(axes) if i != ri]
        mesh = np.meshgrid(*others, indexing="ij")
        base = np.stack(mesh, axis=-1).astype(np.float32)
        out = []
        for r in axes[ri]:
            X = np.insert(base, ri, r, axis=-1)
            out.append(fn(X))
        return np.moveaxis(np.stack(out, axis=0), 0, ri)

    @staticmethod
    def _point(axes, idx) -> np.ndarray:
        return np.array([axes[k][i] for k, i in enumerate(idx)], float)

    def find(self, axes, mask) -> Optional[np.ndarray]:
        idx = np.argwhere(mask)
        if len(idx) == 0:
            return None
        # seeded random witness: spreads probes over the region instead of
        # always returning a corner of the grid
        return self._point(axes, idx[self.rng.integers(len(idx))])

    # ---- queries ----------------------------------------------------------
    @staticmethod
    def _clause_mask(clause: Sequence[Atom], feats) -> np.ndarray:
        m = np.ones(next(iter(feats.values())).shape, bool)
        for a in clause:
            m &= a.eval(feats)
        return m

    def clause_satisfiable(self, clause, rule_atoms) -> CheckResult:
        axes = self._axes(clause, {})
        w = self.find(axes, self._masks(axes, lambda X: self._clause_mask(clause, feature_matrix(X))))
        return CheckResult("clause_satisfiable", "sat" if w is not None else "unsat",
                           [w] if w is not None else [], self.name)

    def nonvacuous(self, rule: Rule) -> List[CheckResult]:
        axes = self._axes(rule.atoms(), {})
        R = self._masks(axes, rule.predict)
        out = []
        for name, m in (("rule_satisfiable", R), ("negation_satisfiable", ~R)):
            w = self.find(axes, m)
            out.append(CheckResult(name, "sat" if w is not None else "unsat",
                                   [w] if w is not None else [], self.name))
        return out

    def monotone(self, rule: Rule, var: str, kind: str) -> CheckResult:
        axes = self._axes(rule.atoms(), {})
        R = self._masks(axes, rule.predict)
        ax = RAW.index(var)
        Rm = np.moveaxis(R, ax, -1)
        # bad pattern along increasing var: never_creates -> F then T ; never_removes -> T then F
        first, second = (~Rm, Rm) if kind == "never_creates" else (Rm, ~Rm)
        seen_before = np.zeros_like(first)
        seen_before[..., 1:] = np.maximum.accumulate(first, axis=-1)[..., :-1]
        hits = np.argwhere(seen_before & second)
        if len(hits) == 0:
            return CheckResult(f"monotone[{var}:{kind}]", "unsat", [], self.name)
        h = hits[self.rng.integers(len(hits))]
        line, j = tuple(h[:-1]), int(h[-1])
        i = int(np.argmax(first[line][:j]))         # earliest opposite value
        def full(k):
            idx = list(line)
            idx.insert(ax, k)
            return self._point(axes, idx)
        x_lo, x_hi = full(i), full(j)
        return CheckResult(f"monotone[{var}:{kind}]", "sat", [x_lo, x_hi], self.name,
                           f"{var}: {x_lo[ax]:.3g} -> {x_hi[ax]:.3g}")

    def boundary_probe(self, rule: Rule, ci: int, aj: int, delta: float, side: str) -> CheckResult:
        clause = rule.clauses[ci]
        atom = clause[aj]
        others = [a for k, a in enumerate(clause) if k != aj]
        axes = self._axes(rule.atoms(), {atom.feature: delta})

        def fn(X):
            f = feature_matrix(X)
            m = self._clause_mask(others, f) & (np.abs(f[atom.feature] - atom.threshold) <= delta + 1e-9)
            R = rule.predict(X)
            return m & (atom.negate().eval(f) & ~R if side == "outside" else atom.eval(f) & R)

        w = self.find(axes, self._masks(axes, fn))
        return CheckResult(f"boundary[{atom.text()}:{side}]", "sat" if w is not None else "unsat",
                           [w] if w is not None else [], self.name)


# ============================================================== z3 backend
class Z3Backend:                                        # pragma: no cover
    """Same queries encoded for Z3 (linear integer/real arithmetic)."""
    name = "z3"

    def __init__(self, space: SearchSpace = DEFAULT_SPACE, timeout_ms: int = 10_000):
        if not HAVE_Z3:
            raise ImportError("z3-solver is not installed")
        self.space, self.timeout_ms = space, timeout_ms
        self.vars = {v.name: v for v in space.variables}

    def _vars(self, tag: str):
        V = {}
        for n in RAW:
            V[n] = z3.Int(f"{n}_{tag}") if self.vars[n].integer else z3.Real(f"{n}_{tag}")
        return V

    def _domain(self, V):
        return [c for n in RAW for c in (V[n] >= self.vars[n].low, V[n] <= self.vars[n].high)]

    @staticmethod
    def _min_la(V):
        return z3.If(V["traffic_rate"] <= V["admission_rate"], V["traffic_rate"], V["admission_rate"])

    def _atom(self, a: Atom, V):
        t = z3.RealVal(str(a.threshold))
        if a.feature == "load_per_replica":
            lhs, rhs = self._min_la(V), t * z3.ToReal(V["replicas"])
        elif a.feature == "admitted_load":
            lhs, rhs = self._min_la(V), t
        else:
            x = V[a.feature]
            lhs, rhs = (z3.ToReal(x) if self.vars[a.feature].integer else x), t
        return lhs > rhs if a.op == ">" else lhs <= rhs

    def _clause(self, clause, V):
        return z3.And(*[self._atom(a, V) for a in clause]) if clause else z3.BoolVal(True)

    def _rule(self, rule: Rule, V):
        return z3.Or(*[self._clause(c, V) for c in rule.clauses]) if rule.clauses else z3.BoolVal(False)

    def _solve(self, constraints, Vs) -> Optional[List[np.ndarray]]:
        s = z3.Solver()
        s.set("timeout", self.timeout_ms)
        s.add(*constraints)
        res = s.check()
        if res == z3.unknown:
            raise TimeoutError("z3 returned unknown")
        if res == z3.unsat:
            return None
        m = s.model()
        out = []
        for V in Vs:
            x = []
            for n in RAW:
                val = m.eval(V[n], model_completion=True)
                x.append(float(val.as_long()) if self.vars[n].integer else float(val.as_fraction()))
            out.append(np.array(x))
        return out

    def clause_satisfiable(self, clause, rule_atoms) -> CheckResult:
        V = self._vars("a")
        w = self._solve(self._domain(V) + [self._clause(clause, V)], [V])
        return CheckResult("clause_satisfiable", "sat" if w else "unsat", w or [], self.name)

    def nonvacuous(self, rule: Rule) -> List[CheckResult]:
        out = []
        for name, neg in (("rule_satisfiable", False), ("negation_satisfiable", True)):
            V = self._vars("a")
            f = self._rule(rule, V)
            w = self._solve(self._domain(V) + [z3.Not(f) if neg else f], [V])
            out.append(CheckResult(name, "sat" if w else "unsat", w or [], self.name))
        return out

    def monotone(self, rule: Rule, var: str, kind: str) -> CheckResult:
        V1, V2 = self._vars("lo"), self._vars("hi")
        same = [V1[n] == V2[n] for n in RAW if n != var]
        R1, R2 = self._rule(rule, V1), self._rule(rule, V2)
        bad = z3.And(z3.Not(R1), R2) if kind == "never_creates" else z3.And(R1, z3.Not(R2))
        w = self._solve(self._domain(V1) + self._domain(V2) + same + [V1[var] < V2[var], bad], [V1, V2])
        ax = RAW.index(var)
        return CheckResult(f"monotone[{var}:{kind}]", "sat" if w else "unsat", w or [], self.name,
                           f"{var}: {w[0][ax]:.3g} -> {w[1][ax]:.3g}" if w else "")

    def boundary_probe(self, rule: Rule, ci: int, aj: int, delta: float, side: str) -> CheckResult:
        clause = rule.clauses[ci]
        atom = clause[aj]
        V = self._vars("a")
        others = [self._atom(a, V) for k, a in enumerate(clause) if k != aj]
        t, d = z3.RealVal(str(atom.threshold)), z3.RealVal(str(delta))
        if atom.feature == "load_per_replica":
            r = z3.ToReal(V["replicas"])
            lo, hi, val = (t - d) * r, (t + d) * r, self._min_la(V)
        elif atom.feature == "admitted_load":
            lo, hi, val = t - d, t + d, self._min_la(V)
        else:
            x = V[atom.feature]
            val = z3.ToReal(x) if self.vars[atom.feature].integer else x
            lo, hi = t - d, t + d
        near = [val >= lo, val <= hi]
        R = self._rule(rule, V)
        side_c = [self._atom(atom.negate(), V), z3.Not(R)] if side == "outside" else [self._atom(atom, V), R]
        w = self._solve(self._domain(V) + others + near + side_c, [V])
        return CheckResult(f"boundary[{atom.text()}:{side}]", "sat" if w else "unsat", w or [], self.name)


# ================================================================== facade
class Verifier:
    """Runs the standard check suite on a rule with the chosen backend."""

    def __init__(self, space: SearchSpace = DEFAULT_SPACE, backend: str = "auto",
                 assumptions=DEFAULT_ASSUMPTIONS, delta_frac: float = 0.02, seed: int = 0):
        if backend == "auto":
            backend = "z3" if HAVE_Z3 else "exact"
        self.backend = Z3Backend(space) if backend == "z3" else ExactBackend(space, seed)
        self.space, self.assumptions, self.delta_frac = space, list(assumptions), delta_frac
        self._ranges = {v.name: v.high - v.low for v in space.variables}
        # load_per_replica range: [min_lambda_a / r_max, max / r_min]
        v = {x.name: x for x in space.variables}
        lo = min(v["traffic_rate"].low, v["admission_rate"].low) / v["replicas"].high
        hi = max(v["traffic_rate"].high, v["admission_rate"].high) / v["replicas"].low
        self._ranges["load_per_replica"] = hi - lo
        self._ranges["admitted_load"] = (max(v["traffic_rate"].high, v["admission_rate"].high)
                                         - min(v["traffic_rate"].low, v["admission_rate"].low))

    def delta(self, feature: str) -> float:
        return self.delta_frac * self._ranges[feature]

    def check_all(self, rule: Rule, boundary: bool = True) -> List[CheckResult]:
        b = self.backend
        out: List[CheckResult] = []
        for i, c in enumerate(rule.clauses):
            r = b.clause_satisfiable(c, rule.atoms())
            r.detail = f"clause {i}"
            out.append(r)
        out += b.nonvacuous(rule)
        for var, kind in self.assumptions:
            out.append(b.monotone(rule, var, kind))
        if boundary:
            for ci, c in enumerate(rule.clauses):
                for aj, a in enumerate(c):
                    for side in ("outside", "inside"):
                        out.append(b.boundary_probe(rule, ci, aj, self.delta(a.feature), side))
        return out
