"""CEGIS: counterexample-guided refinement of operational constraints.

Loop (one iteration):
  1. synthesise a candidate rule from all measured evidence
  2. formal checks (z3_verification): drop UNSAT (dead) clauses; collect
     SAT witnesses of assumption violations and boundary probes
  3. model-based search: configurations where the GP confidently disagrees
     with the rule
  4. validate candidates on the testbed (experiment runner)
  5. a candidate whose measured outcome contradicts the rule is a validated
     counterexample; all measurements are added to the evidence
  6. stop when at most ``tolerance`` (default 10%) of an iteration's tests
     contradict the rule and nothing was pruned, or the budget runs out.
     (Zero is not a usable target: probes placed right on a boundary hit
     measurement noise, so a few boundary disagreements never disappear.)

A SAT witness is never treated as a counterexample by itself: only a
testbed measurement can contradict the rule.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np

from experiment_engine.experiment_config import DEFAULT_SLO, DEFAULT_SPACE, SearchSpace, SLO

from .constraint_synthesis import synthesize, synthesize_cv, synthesize_monotone
from .gaussian_process import OutputGP
from .rules import RAW, Rule
from .z3_verification import CheckResult, Verifier


@dataclass
class Candidate:
    x: np.ndarray
    source: str                   # "monotone" | "boundary" | "gp_disagreement"
    check: str
    rule_says_violation: bool
    pair_id: Optional[int] = None


@dataclass
class Iteration:
    it: int
    rule: Rule
    checks: List[CheckResult]
    candidates: int
    tested: int
    counterexamples: int
    by_source: Dict[str, Tuple[int, int]]       # source -> (tested, counterexamples)
    assumption_violations: List[str]
    pruned_clauses: int
    n_evidence: int
    metrics: Dict[str, float] = field(default_factory=dict)
    converged: bool = False

    def summary(self) -> dict:
        sat = sum(c.sat for c in self.checks)
        return {"iteration": self.it, "rule": self.rule.text(), "rule_size": self.rule.size(),
                "checks_sat": sat, "checks_unsat": len(self.checks) - sat,
                "candidates": self.candidates, "tested": self.tested,
                "counterexamples": self.counterexamples, "by_source": self.by_source,
                "assumption_violations": self.assumption_violations,
                "pruned_clauses": self.pruned_clauses, "n_evidence": self.n_evidence,
                "converged": self.converged,
                **{k: round(v, 4) for k, v in self.metrics.items()}}


class CEGIS:
    def __init__(self, run_fn: Callable[[Dict[str, float], int], dict],
                 space: SearchSpace = DEFAULT_SPACE, slo: SLO = DEFAULT_SLO,
                 verifier: Optional[Verifier] = None, max_depth="cv",
                 max_tests_per_iter: int = 12, gp_candidates: int = 4,
                 gp_confidence: float = 0.8, cex_weight: float = 1.0,
                 min_dist: float = 0.03, tolerance: float = 0.1, seed: int = 0,
                 synthesizer: str = "monotone",
                 evaluate: Optional[Callable[[Rule], Dict[str, float]]] = None):
        self.run_fn, self.space, self.slo = run_fn, space, slo
        self.verifier = verifier or Verifier(space, seed=seed)
        self.max_depth, self.max_tests = max_depth, max_tests_per_iter
        self.gp_k, self.gp_conf, self.cex_weight = gp_candidates, gp_confidence, cex_weight
        self.min_dist = min_dist
        self.synthesizer = synthesizer     # "monotone" (default) or "tree"
        self.tolerance = tolerance       # stop when <= this share of tests contradict the rule
        self.rng = np.random.default_rng(seed)
        self.seed = seed
        self.evaluate = evaluate
        self.X: np.ndarray = np.empty((0, space.dim))
        self.y: np.ndarray = np.empty(0)
        self.w: np.ndarray = np.empty(0)
        self.rows: List[dict] = []
        self.history: List[Iteration] = []
        self._exp_seed = 5_000_000 + seed * 100_000

    # -------------------------------------------------------------- evidence
    def add_evidence(self, X: np.ndarray, y: np.ndarray, weight: float = 1.0) -> None:
        self.X = np.vstack([self.X, np.atleast_2d(X)])
        self.y = np.concatenate([self.y, np.atleast_1d(y)])
        self.w = np.concatenate([self.w, np.full(len(np.atleast_1d(y)), weight)])

    @property
    def violated(self) -> np.ndarray:
        return self.y > self.slo.L95_max_ms

    def synthesize(self) -> Rule:
        if self.synthesizer == "monotone":
            return synthesize_monotone(self.X, self.violated, sample_weight=self.w)
        if self.max_depth == "cv":
            return synthesize_cv(self.X, self.violated, sample_weight=self.w, seed=self.seed)
        return synthesize(self.X, self.violated, max_depth=self.max_depth,
                          sample_weight=self.w, seed=self.seed)

    # ---------------------------------------------------------- candidates
    def _too_close(self, x: np.ndarray, others: List[np.ndarray]) -> bool:
        u = self.space.to_unit(x[None])[0]
        pool = [self.space.to_unit(self.X)] if len(self.X) else []
        if others:
            pool.append(self.space.to_unit(np.array(others)))
        if not pool:
            return False
        P = np.vstack(pool)
        return bool(np.min(np.linalg.norm(P - u, axis=1)) < self.min_dist)

    def _gp_disagreements(self, rule: Rule) -> List[Candidate]:
        if len(self.X) < 8:
            return []
        gp = OutputGP(self.space, n_restarts=1, seed=self.seed).fit(self.X, self.y)
        C = self.space.sample(3000, self.rng)
        p = gp.prob_exceeds(C, self.slo.L95_max_ms)
        r = rule.predict(C)
        conf = np.where(r, 1 - p, p)          # GP confidence that the rule is wrong
        order = np.argsort(-conf)
        out, chosen = [], []
        for i in order:
            if conf[i] < self.gp_conf or len(out) >= self.gp_k:
                break
            if self._too_close(C[i], chosen):
                continue
            chosen.append(C[i])
            out.append(Candidate(C[i], "gp_disagreement", f"P(rule wrong)={conf[i]:.2f}", bool(r[i])))
        return out

    def collect(self, rule: Rule, checks: List[CheckResult]) -> List[Candidate]:
        mono, boundary = [], []
        pair = 0
        for c in checks:
            if not c.sat:
                continue
            if c.check.startswith("monotone"):
                pair += 1
                for w in c.witness:
                    mono.append(Candidate(self.space.from_unit(self.space.to_unit(w[None]))[0],
                                          "monotone", c.check, bool(rule.predict(w[None])[0]), pair))
            elif c.check.startswith("boundary"):
                w = c.witness[0]
                boundary.append(Candidate(w, "boundary", c.check, bool(rule.predict(w[None])[0])))
        self.rng.shuffle(boundary)
        cands, picked = [], []
        # order: assumption witnesses, then model-based (structural misses),
        # then boundary probes (threshold placement)
        for cand in mono + self._gp_disagreements(rule) + boundary:
            if len(cands) >= self.max_tests:
                break
            if self._too_close(cand.x, picked):
                continue
            picked.append(cand.x)
            cands.append(cand)
        return cands

    # ------------------------------------------------------------------ loop
    def iterate(self, it: int) -> Iteration:
        rule = self.synthesize()
        checks = self.verifier.check_all(rule)
        # structural refinement: drop clauses Z3 proves unreachable
        dead = {int(c.detail.split()[-1]) for c in checks
                if c.check == "clause_satisfiable" and not c.sat}
        if dead:
            rule = Rule([c for i, c in enumerate(rule.clauses) if i not in dead], rule.target)

        cands = self.collect(rule, checks)
        by_source: Dict[str, List[int]] = {}
        n_cex = 0
        measured: Dict[int, List[Tuple[np.ndarray, bool]]] = {}
        for cand in cands:
            self._exp_seed += 1
            row = self.run_fn(self.space.as_dict(cand.x), self._exp_seed)
            row["strategy"] = f"cegis_{cand.source}"
            self.rows.append(row)
            viol = row["L95_ms"] > self.slo.L95_max_ms
            is_cex = viol != cand.rule_says_violation
            n_cex += is_cex
            self.add_evidence(cand.x, row["L95_ms"], self.cex_weight if is_cex else 1.0)
            t, k = by_source.get(cand.source, [0, 0])
            by_source[cand.source] = [t + 1, k + int(is_cex)]
            if cand.pair_id is not None:
                measured.setdefault(cand.pair_id, []).append((cand, viol))

        # did the *system* break the encoded assumption (not just the rule)?
        assumption_violations = []
        for pid, pts in measured.items():
            if len(pts) != 2:
                continue
            (c_lo, v_lo), (c_hi, v_hi) = pts
            kind = c_lo.check.split(":")[1].rstrip("]")
            if (kind == "never_creates" and not v_lo and v_hi) or \
               (kind == "never_removes" and v_lo and not v_hi):
                assumption_violations.append(c_lo.check)

        res = Iteration(it, rule, checks, len(cands), len(cands), n_cex,
                        {k: tuple(v) for k, v in by_source.items()},
                        assumption_violations, len(dead), len(self.y))
        self.history.append(res)
        return res

    def run(self, X0: np.ndarray, y0: np.ndarray, max_iters: int = 8,
            max_experiments: Optional[int] = None,
            log: Callable[[str], None] = lambda s: None) -> Rule:
        self.add_evidence(X0, y0)
        start_n = len(self.y)
        r0 = self.synthesize()
        self.initial_rule = r0
        if self.evaluate:
            self.initial_metrics = self.evaluate(r0)
        for it in range(1, max_iters + 1):
            res = self.iterate(it)
            if self.evaluate:
                res.metrics = self.evaluate(self.synthesize())
            log(f"  iter {it}: tested {res.tested}, counterexamples {res.counterexamples}, "
                f"pruned {res.pruned_clauses}" + (f", F1 {res.metrics.get('f1', float('nan')):.3f}"
                                                   if res.metrics else ""))
            if res.pruned_clauses == 0 and res.counterexamples <= self.tolerance * max(res.tested, 1):
                res.converged = True
                break
            if max_experiments and len(self.y) - start_n >= max_experiments:
                break
        self.final_rule = self.synthesize()
        return self.final_rule

    def unresolved(self) -> int:
        """Evidence points the final rule still misclassifies."""
        return int((self.final_rule.predict(self.X) != self.violated).sum())

    @property
    def extra_experiments(self) -> int:
        return len(self.rows)
