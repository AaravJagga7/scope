# SCOPE-X: Flash-sale Shield + OR Engine

My part of SCOPE-X:

* the **Flash-sale Shield**: rate limiter, waiting room, admission control and backpressure (Milestone 1);
* experiments and data (Milestone 2);
* the **OR engine**: a GP and safe Bayesian Optimisation (Milestone 3), then constraint synthesis, Z3 checks and CEGIS refinement (Milestone 4).

Milestone 5 (connecting to the team's Kubernetes testbed, and the 2→4 replica rediscovery) is next.

```
SCOPE-X/
├── flash_sale_shield/        Milestone 1: traffic management
│   ├── config.py             all runtime-configurable parameters (a, B, K, caps...)
│   ├── rate_limiter.py       token bucket (clock passed in -> works live & simulated)
│   ├── waiting_room.py       bounded FIFO queue, max-wait expiry
│   ├── admission_controller.py  in-flight cap, downstream signal, latency guard
│   ├── backpressure.py       queue saturation + downstream saturation signals
│   ├── queue_manager.py      ShieldCore: the decision logic all runtimes share
│   ├── mock_checkout.py      mock Checkout + shared DB connection-pool model
│   ├── runtime.py            asyncio runtime (wall clock)
│   └── main.py               FastAPI service: /checkout /config /stats /metrics
├── experiment_engine/        Milestone 2: experiments and data
│   ├── experiment_config.py  search space x=(λ,r,a,K,B), SLOs, safety limits, workload
│   ├── workload_generator.py flash-sale Poisson arrivals
│   ├── simulator.py          discrete-event testbed surrogate (drives real ShieldCore)
│   ├── experiment_runner.py  run x -> measurement row; emergency stop; CLI
│   ├── data_collector.py     CSV dataset
│   └── locustfile.py         HTTP load for the live FastAPI shield
├── or_engine/                Milestones 3-4: the OR engine
│   ├── gaussian_process.py   GP on log p95 latency, ARD Matern-5/2 + noise
│   ├── bayesian_optimization.py  safe active learner, straddle/UCB/EI/PI, baselines
│   ├── rules.py              rule format: DNF of threshold atoms (+ derived load features)
│   ├── constraint_synthesis.py   evidence -> rules (monotone DNF search, or decision tree)
│   ├── z3_verification.py    Z3 encoding + exact fallback solver for the same rules
│   └── cegis.py              counterexample-guided refinement loop
├── constraint_repository/    validated constraints (constraints.json) + evidence/
├── evaluation/               metrics, BO-vs-random-vs-grid comparison, figures
├── datasets/                 experiments.csv, validation.csv, bo_trace.csv, comparison.csv
├── results/                  summary.json, figures/01..07, pipeline_log.txt
├── tests/                    shield, simulator, rules, solver and CEGIS tests
├── run_pipeline.py           Milestones 1-3 end to end
└── run_milestone4.py         Milestone 4 end to end (needs run_pipeline.py outputs)
```

## How to run

```bash
pip install -r requirements.txt
python -m pytest                 # 16 tests (+1 Z3 cross-check that runs when z3-solver is installed)
python run_pipeline.py           # ~2 min; Milestones 1-3 -> datasets/ and results/
python run_milestone4.py         # ~2 min; Milestone 4 -> constraint_repository/ and results/
                                 # (add --quick to either for smaller budgets)

# single experiment / dataset from the CLI
python -m experiment_engine.experiment_runner single --traffic 600 --replicas 2 --admission 200
python -m experiment_engine.experiment_runner design --kind random --n 40

# live shield
uvicorn flash_sale_shield.main:app --port 8000
curl -X PUT localhost:8000/config -H 'content-type: application/json' -d '{"admission_rate": 150}'
locust -f experiment_engine/locustfile.py --host http://localhost:8000 --headless -u 500 -r 100 -t 30s
```

## Design decisions

**The "testbed" is simulated for now.** The team's Kubernetes testbed doesn't exist yet. A real-time run takes about 16 s, and comparing BO, random and grid search needs hundreds of runs. So `experiment_engine/simulator.py` runs flash-sale experiments on a virtual clock in about 0.1 s each, and they are reproducible from a seed.

The simulator does not re-implement the shield. It drives the same `ShieldCore` object as the FastAPI service. Only the clock and the Checkout model are simulated.

**Checking the simulator against the live code path.** The `inproc` backend runs the real asyncio runtime and mock Checkout on the wall clock. Over a 7 s run the two backends agree closely:

| config | backend | p50 ms | p95 ms | rejection | p95 wait |
|---|---|---|---|---|---|
| λ=150, r=2, a=300 | sim / inproc | 34.0 / 35.6 | 60.6 / 62.5 | 0 / 0 | 0 / 0 |
| λ=400, r=2, a=150, K=300 | sim / inproc | 34.5 / 35.7 | 112 / 133 | 0.40 / 0.40 | 2.0 s / 2.0 s |

**Checkout + DB model.** Each replica has 4 workers. A request holds its worker through the app phase (~20 ms) and also while it waits for and uses a connection from a **shared** DB pool of 8. Write time rises with DB contention. Adding replicas adds workers but no DB connections, so beyond about 4 replicas the bottleneck moves to the database. This is the scale effect SCOPE-X studies.

**GP modelling choices.**
* The GP models **log** p95 latency, because latency spans 50 ms to 10 s and its noise grows with its level.
* Inputs are scaled to the unit cube.
* The kernel has one length-scale per input (ARD), so the learned length-scales show which inputs matter.

**BO acquisition.** The goal is to find the SLO boundary, not a single optimum. So the default acquisition is **straddle** (level-set estimation): `1.96σ − |μ − log L_max|`. EI, PI and UCB are included for comparison and for the later resource-optimisation stage.

**Safety.** BO only proposes a candidate if the GP's upper bound on p95 latency is below `L_stop` (a SafeOpt-style safe set). The experiment controller separately stops a run when windowed p95 latency stays above 1.5 s, or CPU stays above 99.5%. The shield's bounded queue and in-flight caps protect the system regardless of either check.

## Results (simulated testbed, `results/summary.json`)

Initial SLO: p95 checkout latency ≤ 250 ms. 44% of the held-out configurations violate it.

**Shield on vs off.** Flash sale at λ=600 req/s with 2 replicas (graphs 1–4):

| | p95 latency | successful checkouts/s | timeouts | rejected (503) |
|---|---|---|---|---|
| no shield | 12.3 s | 55 | 89% | 0% |
| shield (a=200, K=500, B=100) | 348 ms | 227 | 0% | 55% |

Without the shield, everyone gets in and almost everyone times out. With the shield, about 4× more orders complete, and customers who can't be served get a fast 503 with a `Retry-After` hint. The safety stop catches the no-shield run within 5 s. The demo turns the stop off to show the full collapse.

**GP trained on 40 random experiments, scored on 300 held-out configs:**
* p95 latency prediction: RMSE(log) 0.55, MAE(log) 0.40, median error 29%.
* SLO-violation classification: F1 0.85 (precision 0.92, recall 0.79).
* The 95% intervals cover 88% of held-out points, so the model is slightly over-confident.

**BO vs random vs grid** (3 repeats, same GP, budget 60 experiments, graph 7):

| strategy | F1 @ 60 | precision | recall | RMSE(log) | experiments to mean F1 ≥ 0.9 |
|---|---|---|---|---|---|
| safe BO (straddle) | **0.91** | **0.96** | 0.87 | **0.51** | **50** |
| random search | 0.89 | 0.92 | 0.87 | 0.52 | not reached in 60 |
| grid search | 0.87 | 0.93 | 0.82 | 0.58 | not reached in 60 |

How to read the comparison:
* BO wins at 40–60 experiments and is the only strategy to reach F1 0.9.
* Random search is ahead below about 25 experiments, because BO's first 8 points are deliberately conservative (lower half of λ).
* The margin over random search is modest, and with 3 repeats the min–max bands overlap. Run more repeats before claiming significance.

**Findings worth carrying into constraint synthesis:**
* The GP learned that p95 latency barely depends on queue capacity K (length-scale ≈ 6–8). It depends strongly on r, a and λ.
* Setting a ≈ 110·r (nominal capacity) still breaks the 250 ms SLO (graph 4). The admission rate has to sit below Checkout's real capacity.
* p95 latency settles at plateaus fixed by the in-flight cap (`downstream_limit`). This is backpressure doing its job.

## Milestone 4: constraint synthesis, Z3 and CEGIS

**Rule format** (`or_engine/rules.py`). A constraint is an OR of AND-clauses over threshold atoms, e.g. `(min(λ,a)/r > 102) OR (min(λ,a) > 346 AND min(λ,a)/r > 67)  ⇒  p95 > 250 ms`. Besides the raw variables, two derived features describe what Checkout actually receives:
* `admitted_load = min(λ, a)`: the rate reaching Checkout, and so the shared database;
* `load_per_replica = min(λ, a) / r`: the rate each replica has to absorb.

Because `r` is an integer, every rule stays in linear arithmetic, which Z3 decides.

**Synthesis** (`constraint_synthesis.py`). Rules are learned only from measured experiments, never from GP predictions. The default synthesiser is a greedy F1 search over **monotone** rules. Its atoms only point in the directions of the domain assumptions: more load, more burst or fewer replicas can only make things worse. A decision-tree synthesiser is kept as an ablation. The tree did worse inside CEGIS (see below), because points added near the boundary pull its splits into odd shapes.

**Formal checks** (`z3_verification.py`). The checks run on the encoded model: the bounded domain, the rule and the assumptions. In this build they were executed by the exact fallback solver. The Z3 encoding is written but has not been executed (see "Solver backend").
* clause reachability: an UNSAT clause is dead and gets dropped;
* non-vacuity: both outcomes must be possible;
* monotonicity assumptions: more replicas never *creates* a violation; more traffic, admission rate or bucket size never *removes* one;
* boundary probes: a configuration within 2% of each threshold, on each side.

A SAT witness is only a *candidate*. Only a testbed run can count as a counterexample.

**Solver backend.** Z3 is not installed in this build environment, so every result below comes from the **exact fallback solver**. Once `r` is fixed, every atom becomes an axis-aligned bound. So truth is constant between consecutive threshold values, and checking one point per interval decides each query exactly. The Z3 encoding of the same queries is in `Z3Backend` and is used automatically once `pip install z3-solver` is run. `tests/test_constraints.py::test_z3_matches_exact_backend` then checks the two backends agree. That test was skipped here.

**CEGIS loop** (`cegis.py`). Each iteration runs these steps:
1. synthesise a rule;
2. run the formal checks;
3. add model-based candidates, i.e. points where the GP confidently disagrees with the rule;
4. run up to 8 candidates on the testbed;
5. add every measurement to the evidence;
6. stop when ≤ 10% of an iteration's tests contradict the rule, or the budget runs out.

### Results (simulated testbed, `results/milestone4_summary.json`)

**Refinement vs random extra data** (graph 8; both arms use the monotone synthesiser). Each repeat starts from 15 random experiments, then spends about 38 extra experiments over 6 repeats. All rules are scored on the 300 held-out configurations.

| | held-out F1 (mean, min–max over 6 repeats) |
|---|---|
| before refinement | 0.82 |
| CEGIS, monotone synthesiser | **0.90** (0.87–0.92) |
| same number of random experiments | 0.88 (0.82–0.91) |
| CEGIS, decision-tree synthesiser (ablation) | 0.86 (0.79–0.91) |
| random experiments, tree | 0.91 (0.90–0.93) |

* With the monotone synthesiser, CEGIS gains accuracy faster than random data. Its worst repeat (0.87) beats random's worst (0.82).
* With the tree synthesiser, CEGIS is *worse* than random data. The search only helps when the rule language can use the points it adds near the boundary.
* The final gap (0.90 vs 0.88) is small next to the spread across repeats. Treat it as a trend, not a significant result.
* Counterexample hit rate: GP-disagreement candidates contradicted the rule 77% of the time; solver boundary probes did 34% of the time. No monotonicity witness came up, because monotone rules satisfy the assumptions by construction. The exact solver confirms this: all four assumption checks are UNSAT (Z3 confirmation pending). UNSAT is a property of the encoded model, not of the real system.

**Constraint C1** (starting from the 60 BO experiments, stored in `constraint_repository/constraints.json`):

```
before: IF min(λ,a)/r > 96  OR (min(λ,a) > 327 AND min(λ,a)/r > 66)  OR (r ≤ 3 AND B > 160)        F1 0.88
after:  IF min(λ,a)/r > 102 OR (r ≤ 2 AND B > 160) OR (min(λ,a) > 346 AND min(λ,a)/r > 67)  THEN p95 > 250 ms   F1 0.91
```

The clauses are candidate relationships between configuration and SLO violation. They don't establish *why* a violation happens:
* **Clause 1:** too much admitted traffic per replica.
* **Clause 2:** a large burst allowance (token bucket) on a small deployment.
* **Clause 3:** high total admitted load *together with* moderately high per-replica load. It depends on the replica count through the second condition.

A possible explanation for clause 3 is the shared database. Simulator measurements on the 300 held-out configurations are consistent with it:

| held-out configs matching... | n | violation rate | median DB util | median CPU util | replicas |
|---|---|---|---|---|---|
| clause 1 only | 30 | 97% | 0.30 | 0.51 | 1–3 |
| clause 3 only | 25 | 64% | 0.77 | 0.36 | 4–6 |
| no clause | 164 | 5% | 0.30 | 0.23 | 1–6 |

Where only clause 3 fires, the database runs much busier and the CPU less busy. But two caveats apply:
* the simulator's shared DB pool *was built* to create this effect, so this is not evidence about the real system;
* clause 3 is imprecise: 36% of the configurations it flags met the SLO.

Confirming the mechanism needs database metrics from the Kubernetes testbed: pool wait time, write latency and connection utilisation.

The refinement ran 5 iterations and 40 experiments, and found 24 counterexamples. Formal checks (exact solver): 67 SAT, 26 UNSAT. 6 evidence points remain unresolved, i.e. still misclassified by the final rule. The status is **candidate**, not validated: the pre-set criterion was held-out F1 ≥ 0.85 *and* ≤ 5% unresolved, and 6 out of 100 evidence points (6%) misses the second part. I left the criterion as set.

## What has *not* been verified yet

* **Z3 itself was not run** (it wasn't installable here). The candidate constraint was checked using an exact fallback decision procedure. A corresponding Z3 encoding is implemented but still needs to be executed and cross-validated; investigate any mismatch rather than assuming the two are equivalent. All formal results come from the exact fallback solver. The Z3 encoding is written and is tested against the fallback whenever z3-solver is installed.
* `main.py` (FastAPI) and `locustfile.py` were written but **not run**. FastAPI and Locust weren't installed in the build environment. The asyncio runtime they wrap *was* run and checked (see the table above).
* All numbers come from the simulated testbed, using first-guess service times. Re-calibrate the ranges in `experiment_config.py` and the service times in `CheckoutConfig` against the team's Kubernetes testbed. Then re-run.
* The reference "ground truth" uses one noisy run per configuration. Labels near the boundary are slightly noisy.

## Next (Milestone 5)

1. Install `z3-solver` and run `pytest`, so the Z3 backend is cross-checked against the exact solver.
2. Keep the current simulated datasets and results as the **simulation baseline**. Write real-testbed measurements to separate files (e.g. `datasets/k8s/`, `results/k8s/`) so simulation and real-system results can be compared cleanly.
3. Connect the `k8s` backend in `experiment_runner.py` to the team's testbed, recalibrate the ranges, and re-run both pipelines on real measurements.
   Record database metrics (pool wait, write latency, connection utilisation) so clause-3-type explanations can be tested.
4. Scale-aware rediscovery. Learn C1 at r = 2, scale to r = 4, then compare three approaches on accuracy and experiment cost (graphs 9–10):
   * reuse the old rule as is;
   * full rediscovery (BO + CEGIS from scratch);
   * incremental rediscovery: keep the evidence, and re-check only the clauses whose boundaries depend on r (boundary probes and GP disagreement at the new scale).
