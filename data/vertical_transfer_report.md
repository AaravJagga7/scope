# Task 14D: constraints under vertical scaling

> Simulated study (queuing model + serial/parallel CPU service model + working-set memory/OOM model + CFS throttling model). Not a hardware measurement.

Design: 720 cells x 2 repeats = 1440 runs; region: `{"cpu_limit_cores": [0.5, 1.0, 2.0], "memory_limit_mib": [512, 1024, 2048], "checkout_replicas": [2, 4], "db_pool_sizes": [50, 100], "cache_enabled": [true, false], "target_rps": [150, 1000], "rps_levels": [150, 250, 350, 425, 475, 500, 550, 650, 800, 1000], "workload": "flash_sale request mix, constant offered load", "duration_seconds": 30, "repeats": 2}`

## 1. Per-replica capacity vs CPU limit

- Fit `mu(s) = mu_0 * s^alpha` on 642 saturated runs: **alpha = 0.733**, 95% CI [0.728, 0.739] (cluster bootstrap [0.6948, 0.7741]), mu_0 = 119.2 rps at 1 core, R² = 0.991.
- Linear scaling (alpha = 1): t = -96.76, p = 0 -> **rejected** at 5%.
- Largest-memory-only fit (no GC pressure): alpha = 0.756 [0.751, 0.760], n = 304.
- Local slopes: 0.5->1 cores: 0.811, 1->2 cores: 0.684. A falling slope means a power law is only an approximation: returns diminish as the serial part dominates.
- Memory offsets (log capacity vs largest limit): mem_512Mi: -0.047 [-0.060, -0.033], mem_1024Mi: -0.131 [-0.137, -0.125]
- Check against the generating model: true capacities {'0.5': 70.0, '1.0': 122.81, '2.0': 197.18}, true least-squares alpha 0.747.
- Plot: `vertical_capacity_vs_cpu.png`

## 2. Constraint classification

| ID | Constraint (condition => SLO violation) | Class | Steady P/R (n) | Old -> pooled bound [95% CI] | Bound by CPU limit | Class by CPU | Evidence / notes |
|---|---|---|---|---|---|---|---|
| C01 | `target_rps > 487.50 and cache_enabled <= 0.50` | **shifted** | P 0.84 / R 0.30 (n=1350) | 488 -> 350 [350, 350] | 0.5c: 150; 1c: 150; 2c: 350 | 0.5c shifted, 1c shifted, 2c shifted | new bound 350 with 95% CI [350, 350] excludes old 488 (28% shift). |
| C02 | `0.00093 * target_rps - 0.00666 * checkout_replicas + +0.00157 * db_pool_size - 0.15622 * cache_enabled >= 0.4311` | **shifted** | P 0.83 / R 0.78 (n=3565) | 0.431 -> 0.3 [0.3, 0.3] | 0.5c: 0.191; 1c: 0.291; 2c: 0.494 | 0.5c shifted, 1c shifted, 2c shifted | new bound 0.3 with 95% CI [0.3, 0.3] excludes old 0.431 (30% shift). |
| C03 | `0.00087 * target_rps - 0.01911 * checkout_replicas + +0.00167 * db_pool_size - 0.37137 * cache_enabled >= 0.2423` | **shifted** | P 0.79 / R 0.68 (n=3286) | 0.242 -> -0.0598 [-0.0598, -0.0598] | 0.5c: -0.147; 1c: -0.0598; 2c: 0.312 | 0.5c shifted, 1c shifted, 2c shifted | new bound -0.0598 with 95% CI [-0.0598, -0.0598] excludes old 0.242 (125% shift). |
| C04 | `0.00089 * target_rps - 0.01413 * checkout_replicas + +0.00163 * db_pool_size - 0.28531 * cache_enabled >= 0.3178` | **shifted** | P 0.79 / R 0.69 (n=3349) | 0.318 -> 0.0512 [0.0512, 0.0512] | 0.5c: -0.0378; 1c: 0.0512; 2c: 0.418 | 0.5c shifted, 1c shifted, 2c shifted | new bound 0.0512 with 95% CI [0.0512, 0.0512] excludes old 0.318 (84% shift). |
| C05 | `0.00097 * target_rps + +0.00150 * db_pool_size >= 0.5540` | **shifted** | P 0.82 / R 0.72 (n=3349) | 0.554 -> 0.318 [0.318, 0.393] | 0.5c: 0.221; 1c: 0.295; 2c: 0.489 | 0.5c shifted, 1c shifted, 2c shifted | new bound 0.318 with 95% CI [0.318, 0.393] excludes old 0.554 (43% shift). |
| C06 | `0.00097 * target_rps + +0.00144 * db_pool_size >= 0.5506` | **shifted** | P 0.82 / R 0.72 (n=3349) | 0.551 -> 0.484 [0.484, 0.484] | 0.5c: 0.217; 1c: 0.289; 2c: 0.483 | 0.5c shifted, 1c shifted, 2c shifted | new bound 0.484 with 95% CI [0.484, 0.484] excludes old 0.551 (12% shift). |
| C07 | `target_rps > 487.50 and cache_enabled > 0.50 and db_pool_size > 22.50 and target_rps <= 862.50` | **shifted** | P 0.85 / R 0.31 (n=1405) | 488 -> 250 [250, 350] | 0.5c: 250; 1c: 150; 2c: 350 | 0.5c shifted, 1c shifted, 2c shifted | new bound 250 with 95% CI [250, 350] excludes old 488 (49% shift). |
| C08 | `target_rps > 487.50 and cache_enabled > 0.50 and db_pool_size > 22.50` | **shifted** | P 0.86 / R 0.35 (n=1567) | 488 -> 350 [250, 350] | 0.5c: 250; 1c: 150; 2c: 350 | 0.5c shifted, 1c shifted, 2c shifted | new bound 350 with 95% CI [250, 350] excludes old 488 (28% shift). |
| C09 | `target_rps <= 487.50 and cache_enabled <= 0.50 and checkout_replicas <= 3.50` | **shifted** | P 0.62 / R 0.14 (n=855) | 488 -> 1e+03 [1e+03, 1e+03] | 0.5c: n/a; 1c: 1e+03; 2c: 1e+03 | 0.5c unchanged, 1c shifted, 2c shifted | new bound 1e+03 with 95% CI [1e+03, 1e+03] excludes old 488 (105% shift). Class depends on CPU limit: 0.5 cores=unchanged, 1 cores=shifted, 2 cores=shifted. |
| C10 | `target_rps <= 487.50 and cache_enabled <= 0.50 and checkout_replicas <= 3.50 and target_rps <= 287.50` | **disappeared** | P 0.33 / R 0.04 (n=445) | 488 -> 250 [250, 250] | 0.5c: n/a; 1c: n/a; 2c: n/a | 0.5c unchanged, 1c disappeared, 2c disappeared | rule does not predict observed violations (precision 0.33, static 0.00). Confirmed after 6 boundary probes; closest point tested: 150.0 rps, 2 replicas, pool 50, cache off, 0.5 cores / 512Mi (distance to rule region 0.0, gap to boundary 0.0). Tested region: {"cpu_limit_cores": [0.5, 1.0, 2.0], "memory_limit_mib": [512, 1024, 2048], "checkout_replicas": [2, 4], "db_pool_sizes": [50, 100], "cache_enabled": [true, false], "target_rps": [150, 1000], "rps_levels": [150, 250, 350, 425, 475, 500, 550, 650, 800, 1000], "workload": "flash_sale request mix, constant offered load", "duration_seconds": 30, "repeats": 2}. Class depends on CPU limit: 0.5 cores=unchanged, 1 cores=disappeared, 2 cores=disappeared. |
| K02 | `rps_per_replica > 60.71` | **shifted** | P 0.64 / R 1.00 (n=5925) | 60.7 -> 106 [106, 106] | 0.5c: 62.5; 1c: 106; 2c: 175 | 0.5c unchanged, 1c shifted, 2c shifted | new bound 106 with 95% CI [106, 106] excludes old 60.7 (75% shift). Class depends on CPU limit: 0.5 cores=unchanged, 1 cores=shifted, 2 cores=shifted. |
| K03 | `rps_per_pool_conn > 14.00 and cache_enabled <= 0.50` | **unchanged** | P 1.00 / R 0.07 (n=270) | 14 -> 13 [13, 13] | 0.5c: 3; 1c: 8.5; 2c: 13 | 0.5c shifted, 1c shifted, 2c shifted | precision 1.00 (static 1.00), recall 0.07, support 270 |
| V01 | `target_rps / (checkout_replicas * cpu_cores ** 0.733) > 106.25` | **new (vertical)** | P 1.00 / R 1.00 (n=1938) | n/a | n/a | n/a | Needs the resource limits to express; held-out steady rows. |
| V02 | `target_rps > 387.50 and cpu_cores <= 1.50` | **new (vertical)** | P 0.96 / R 0.62 (n=2485) | n/a | n/a | n/a | Needs the resource limits to express; held-out steady rows. |
| V03 | `target_rps > 387.50 and cpu_cores <= 1.50 and target_rps > 450.00` | **new (vertical)** | P 1.00 / R 0.58 (n=2215) | n/a | n/a | n/a | Needs the resource limits to express; held-out steady rows. |
| V04 | `target_rps > 387.50 and cpu_cores > 1.50 and checkout_replicas <= 3.00` | **new (vertical)** | P 1.00 / R 0.16 (n=594) | n/a | n/a | n/a | Needs the resource limits to express; held-out steady rows. |

Static (500m) bounds of the mechanism constraints:
- K02: rps_per_replica > 60.7143 [95% CI 56.25, 60.7143], F1 1.0
- K03: rps_per_pool_conn > 14.0 [95% CI 7.2222, 20.0], F1 0.7778

## 3. Does CPU saturation stop binding? Does the DB constraint stay?

| CPU limit | Steady rows | SLO violations | CPU-bound | DB-bound | CPU-bound share |
|---|---|---|---|---|---|
| 0.5 | 9338 | 8042 | 6941 | 1101 | 0.86 |
| 1 | 10432 | 7192 | 5895 | 1297 | 0.82 |
| 2 | 11199 | 4071 | 3529 | 542 | 0.87 |

- K02 (CPU saturation) by CPU limit: 0.5c: 62.5; 1c: 106; 2c: 175; classes {'0.5': 'unchanged', '1': 'shifted', '2': 'shifted'}.
- K03 (DB saturation) by CPU limit: 0.5c: 3; 1c: 8.5; 2c: 13; classes {'0.5': 'shifted', '1': 'shifted', '2': 'shifted'}.

## 4. DB connections per replica vs CPU and memory limits

- Matched offered load (n=30969, R²=0.8159): cpu_cores=1: -0.842 [-0.865, -0.820] *, cpu_cores=2: -2.030 [-2.052, -2.007] *, memory_mib=1024: +0.224 [+0.196, +0.252] *, memory_mib=2048: +0.218 [+0.190, +0.246] *
- Adjusted for frontend utilisation (n=30969, R²=0.9956): cpu_cores=1: +0.007 [+0.003, +0.011] *, cpu_cores=2: +0.022 [+0.017, +0.027] *, memory_mib=1024: -0.015 [-0.020, -0.011] *, memory_mib=2048: -0.021 [-0.026, -0.017] *, utilisation: +6.059 [+6.048, +6.069] *
- Example, 350 rps on 4 replicas, mean connections per replica by CPU: {'0.5': 7.202, '1': 4.275, '2': 2.658}
- At saturation (utilisation >= 1): 0.5c/512Mi mean 6.785, max 7.4, 0.5c/1024Mi mean 7.158, max 7.45, 0.5c/2048Mi mean 7.156, max 7.45, 1c/512Mi mean 6.22, max 7.325, 1c/1024Mi mean 7.016, max 7.45, 1c/2048Mi mean 7.008, max 7.45, 2c/512Mi mean 6.329, max 7.45, 2c/1024Mi mean 7.021, max 7.45, 2c/2048Mi mean 7.001, max 7.45

## Pod restarts and throttling

- Total OOM restarts: 2376
| Memory | CPU | Runs | Runs with restarts | Restarts |
|---|---|---|---|---|
| 512Mi | 0.5 | 160 | 144 | 1176 |
| 512Mi | 1 | 160 | 96 | 744 |
| 512Mi | 2 | 160 | 72 | 456 |
| 1Gi | 0.5 | 160 | 0 | 0 |
| 1Gi | 1 | 160 | 0 | 0 |
| 1Gi | 2 | 160 | 0 | 0 |
| 2Gi | 0.5 | 160 | 0 | 0 |
| 2Gi | 1 | 160 | 0 | 0 |
| 2Gi | 2 | 160 | 0 | 0 |
- Mean throttled CFS periods by CPU limit: {'0.5': 72.22, '1': 47.7, '2': 26.39}
