import numpy as np
import pytest

from experiment_engine.experiment_config import DEFAULT_SPACE as S
from experiment_engine.experiment_runner import run_experiment
from or_engine.cegis import CEGIS
from or_engine.constraint_synthesis import synthesize, synthesize_monotone
from or_engine.rules import RAW, Atom, Rule, feature_matrix
from or_engine.z3_verification import HAVE_Z3, ExactBackend, Verifier

MONO = Rule([[Atom("load_per_replica", ">", 100)], [Atom("admitted_load", ">", 400)]])
NON_MONO = Rule([[Atom("replicas", ">", 3)]])           # "more replicas -> violation"


def test_rule_predict_and_roundtrip():
    X = np.array([[600, 2, 300, 500, 100],   # load/r = 150 -> violation
                  [600, 6, 300, 500, 100],   # load/r = 50, load 300 -> safe
                  [800, 6, 500, 500, 100]])  # load 500 > 400 -> violation
    assert MONO.predict(X).tolist() == [True, False, True]
    assert Rule.from_dict(MONO.to_dict()).predict(X).tolist() == [True, False, True]


def test_synthesis_recovers_planted_rule():
    rng = np.random.default_rng(0)
    X = S.sample(300, rng)
    y = MONO.predict(X)
    for rule in (synthesize(X, y, max_depth=2), synthesize_monotone(X, y)):
        assert (rule.predict(X) == y).mean() > 0.97


def test_exact_monotone_checks():
    v = Verifier(S, backend="exact")
    assert not v.backend.monotone(MONO, "replicas", "never_creates").sat
    assert not v.backend.monotone(MONO, "traffic_rate", "never_removes").sat
    r = v.backend.monotone(NON_MONO, "replicas", "never_creates")
    assert r.sat
    lo, hi = r.witness
    assert lo[1] < hi[1] and not NON_MONO.predict(lo[None])[0] and NON_MONO.predict(hi[None])[0]


def test_exact_clause_unsat_and_integer_boundary():
    b = ExactBackend(S)
    # a <= 600, so min(lambda, a)/r can be at most 600 -> unreachable above it
    assert not b.clause_satisfiable([Atom("load_per_replica", ">", 650)], []).sat
    assert b.clause_satisfiable([Atom("load_per_replica", ">", 550)], []).sat
    # no integer replica count within 0.1 of 2.5
    rule = Rule([[Atom("replicas", "<=", 2.5)]])
    assert not b.boundary_probe(rule, 0, 0, 0.1, "outside").sat


def test_boundary_witness_is_near_threshold_and_outside():
    b = ExactBackend(S, seed=3)
    r = b.boundary_probe(MONO, 0, 0, 10.0, "outside")
    assert r.sat
    w = r.witness[0]
    lpr = feature_matrix(w[None])["load_per_replica"][0]
    assert 90 - 1e-6 <= lpr <= 100 and not MONO.predict(w[None])[0]


@pytest.mark.skipif(not HAVE_Z3, reason="z3-solver not installed")
def test_z3_matches_exact_backend():
    z, e = Verifier(S, backend="z3"), Verifier(S, backend="exact")
    for rule in (MONO, NON_MONO):
        zs = [c.status for c in z.check_all(rule)]
        es = [c.status for c in e.check_all(rule)]
        assert zs == es


def test_cegis_smoke():
    X0 = S.sample(12, np.random.default_rng(1))
    y0 = np.array([run_experiment(S.as_dict(x), seed=i)["L95_ms"] for i, x in enumerate(X0)])
    c = CEGIS(lambda x, s: run_experiment(x, seed=s), S, max_tests_per_iter=4, seed=0)
    rule = c.run(X0, y0, max_iters=2)
    assert c.extra_experiments <= 8 and len(c.history) >= 1
    assert isinstance(rule, Rule) and len(c.y) == 12 + c.extra_experiments
