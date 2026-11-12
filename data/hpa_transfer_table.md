| ID | Constraint (condition => SLO violation) | Class | Steady P/R (n) | Transition P/R (n) | Old -> New bound [95% CI] | Refit exps | Evidence / notes |
|---|---|---|---|---|---|---|---|
| C01 | `target_rps > 487.50 and cache_enabled <= 0.50` | **disappeared** | P 0.00 / R 0.00 (n=0) | P 0.00 / R 0.00 (n=0) | 487.50 -> n/a (context clauses not satisfied in the tested HPA region) | 0 | rule has zero support in the tested HPA region. cache_enabled is fixed True in the HPA region, so cache-off branches cannot be exercised; disappearance here is a region limitation, not an HPA effect. Tested region: RPS 0.3-1.0 x 500, replicas dynamic 1-10, pool [50, 100], workloads ['ramp', 'flash_sale', 'sinusoidal'], cache on. |
| C02 | `0.00093 * target_rps - 0.00666 * checkout_replicas + +0.00157 * db_pool_size - 0.15622 * cache_enabled >= 0.4311` | **shifted** | P 0.74 / R 0.36 (n=105) | P 0.67 / R 0.44 (n=144) | 0.43 -> 0.35 [0.35, 0.35] | 0 | new bound 0.347 with 95% CI [0.345, 0.347] excludes old 0.431 (20% shift). cache_enabled is fixed True in the HPA region, so cache-off branches cannot be exercised; disappearance here is a region limitation, not an HPA effect. |
| C03 | `0.00087 * target_rps - 0.01911 * checkout_replicas + +0.00167 * db_pool_size - 0.37137 * cache_enabled >= 0.2423` | **shifted** | P 0.00 / R 0.00 (n=0) | P 0.00 / R 0.00 (n=0) | 0.24 -> 0.07 [0.03, 0.07] | 3 | new bound 0.0671 with 95% CI [0.0315, 0.0671] excludes old 0.242 (72% shift). Previous condition has zero support in the HPA region, so only the re-fitted bound is usable there. cache_enabled is fixed True in the HPA region, so cache-off branches cannot be exercised; disappearance here is a region limitation, not an HPA effect. Re-fit used 3 GP-selected extra run(s); CI after re-fit [0.0671, 0.0671]. |
| C04 | `0.00089 * target_rps - 0.01413 * checkout_replicas + +0.00163 * db_pool_size - 0.28531 * cache_enabled >= 0.3178` | **shifted** | P 0.00 / R 0.00 (n=0) | P 0.00 / R 0.00 (n=0) | 0.32 -> 0.16 [0.16, 0.18] | 5 | new bound 0.156 with 95% CI [0.156, 0.181] excludes old 0.318 (51% shift). Previous condition has zero support in the HPA region, so only the re-fitted bound is usable there. cache_enabled is fixed True in the HPA region, so cache-off branches cannot be exercised; disappearance here is a region limitation, not an HPA effect. Re-fit used 5 GP-selected extra run(s); CI after re-fit [0.156, 0.181] (budget exhausted before reaching target CI width). |
| C05 | `0.00097 * target_rps + +0.00150 * db_pool_size >= 0.5540` | **unchanged** | P 0.61 / R 1.00 (n=358) | P 0.57 / R 0.95 (n=362) | 0.55 -> 0.56 [0.56, 0.56] | 0 | precision 0.61 (static 0.58), recall 1.00, support 358 |
| C06 | `0.00097 * target_rps + +0.00144 * db_pool_size >= 0.5506` | **unchanged** | P 0.61 / R 1.00 (n=358) | P 0.57 / R 0.95 (n=362) | 0.55 -> 0.55 [0.55, 0.55] | 0 | precision 0.61 (static 0.58), recall 1.00, support 358 |
| C07 | `target_rps > 487.50 and cache_enabled > 0.50 and db_pool_size > 22.50 and target_rps <= 862.50` | **unchanged** | P 0.76 / R 1.00 (n=290) | P 0.76 / R 0.95 (n=274) | 487.50 -> 491.40 [491.40, 491.40] | 0 | precision 0.76 (static 0.33), recall 1.00, support 290. cache_enabled is fixed True in the HPA region, so cache-off branches cannot be exercised; disappearance here is a region limitation, not an HPA effect. |
| C08 | `target_rps > 487.50 and cache_enabled > 0.50 and db_pool_size > 22.50` | **unchanged** | P 0.76 / R 1.00 (n=290) | P 0.76 / R 0.95 (n=274) | 487.50 -> 491.40 [491.40, 491.40] | 0 | precision 0.76 (static 0.29), recall 1.00, support 290. cache_enabled is fixed True in the HPA region, so cache-off branches cannot be exercised; disappearance here is a region limitation, not an HPA effect. |
| C09 | `target_rps <= 487.50 and cache_enabled <= 0.50 and checkout_replicas <= 3.50` | **disappeared** | P 0.00 / R 0.00 (n=0) | P 0.00 / R 0.00 (n=0) | 487.50 -> n/a (context clauses not satisfied in the tested HPA region) | 0 | rule has zero support in the tested HPA region. cache_enabled is fixed True in the HPA region, so cache-off branches cannot be exercised; disappearance here is a region limitation, not an HPA effect. Tested region: RPS 0.3-1.0 x 500, replicas dynamic 1-10, pool [50, 100], workloads ['ramp', 'flash_sale', 'sinusoidal'], cache on. |
| C10 | `target_rps <= 487.50 and cache_enabled <= 0.50 and checkout_replicas <= 3.50 and target_rps <= 287.50` | **disappeared** | P 0.00 / R 0.00 (n=0) | P 0.00 / R 0.00 (n=0) | 487.50 -> n/a (context clauses not satisfied in the tested HPA region) | 0 | rule has zero support in the tested HPA region. cache_enabled is fixed True in the HPA region, so cache-off branches cannot be exercised; disappearance here is a region limitation, not an HPA effect. Tested region: RPS 0.3-1.0 x 500, replicas dynamic 1-10, pool [50, 100], workloads ['ramp', 'flash_sale', 'sinusoidal'], cache on. |
| K01 | `checkout_replicas * 6.00 - db_pool_size > 0` | **violated-by-autoscaler** | P 0.00 / R 0.00 (n=0) | P 0.00 / R 0.00 (n=0) | n/a | 0 | 275 violating rows; HPA scale-up exceeded pool/6. Invariant derived from the simulator's calibrated connection demand (6 per replica); not a learned statistical rule. No static held-out precision recorded in the validated set. |
| N01 | `target_rps > 495.70 and checkout_replicas <= 7.50 and checkout_replicas <= 2.50` | **new (transient)** | n/a | P 0.86 / R 0.44 (n=111) | n/a | 0 | Learned on transition rows only; absent/weaker in steady state |

## Connection invariant: replicas x connections-per-replica vs pool

- Invariant: `checkout_replicas * 6 <= db_pool_size`
- Total violating rows: **275**
- Affected runs: **17**
- Violating rows that also breached the latency SLO: **3**

| Workload | CPU target | Max replicas | Pool | Violating rows | First t (s) | Peak replicas | Peak demand | SLO violated rows |
|---|---|---|---|---|---|---|---|---|
| flash_sale | 50 | 10 | 50 | 60 | 20 | 10 | 60.0 | 0 |
| flash_sale | 70 | 10 | 50 | 45 | 25 | 10 | 60.0 | 3 |
| ramp | 50 | 10 | 50 | 60 | 20 | 10 | 60.0 | 0 |
| ramp | 70 | 10 | 50 | 20 | 30 | 10 | 60.0 | 0 |
| sinusoidal | 50 | 10 | 50 | 60 | 20 | 10 | 60.0 | 0 |
| sinusoidal | 70 | 10 | 50 | 30 | 25 | 9 | 54.0 | 0 |

Causal autoscaler actions (scale-ups immediately preceding violations):
- scale_up 8->10 at t=20s (CPU 88.4% vs target 50%, max 10) -> 40 violating rows
- scale_up 9->10 at t=25s (CPU 78.9% vs target 50%, max 10) -> 30 violating rows
- scale_up 8->10 at t=20s (CPU 88.8% vs target 50%, max 10) -> 20 violating rows
- scale_up 5->10 at t=20s (CPU 91.2% vs target 50%, max 10) -> 20 violating rows
- scale_up 9->10 at t=25s (CPU 61.0% vs target 50%, max 10) -> 15 violating rows
- scale_up 9->10 at t=25s (CPU 59.7% vs target 50%, max 10) -> 15 violating rows
- scale_up 9->10 at t=25s (CPU 55.7% vs target 50%, max 10) -> 15 violating rows
- scale_up 7->10 at t=25s (CPU 94.9% vs target 70%, max 10) -> 15 violating rows

HPA scaled replicas past the pool's connection budget. The queuing simulator clamps active connections at the pool size, so a structural connection violation is a configuration-level failure and does not automatically appear as a p95 latency violation.
