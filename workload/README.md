# Workload (`workload/`)

Synthetic workload generation and traffic shaping using Locust for operational constraint exploration on Kubernetes and local microservices.

## Profiles

| Profile | Behavior & Characteristics | Primary Bottleneck Stressed |
|---|---|---|
| `normal` | Realistic user journey: 50% browse catalog, 30% add to cart, 20% checkout. Think time: 0.1s - 0.5s. | Balanced baseline load |
| `flash_sale` | Instantaneous traffic spike: 80% checkout attempts immediately with near-zero think time (<20ms). | Checkout throughput & concurrency limits |
| `ramp` | Linear user ramp-up from 10% to 100% of target RPS over test duration. | Breaking point & saturation discovery |
| `viral_product` | 95%+ of traffic concentrated on a single hot item (`prod-1`). | Product Redis cache & Inventory row lock contention |
| `slow_payment_api` | High checkout volume stressing upstream microservices when downstream payment latency degrades. | Thread pool & connection pool exhaustion |

## Running Workloads

Execute workloads using the `workload/run.py` CLI:

```bash
# Flash sale spike experiment (Acceptance Command)
python workload/run.py --profile flash_sale --rps 1000 --duration 120

# Standard browsing profile
python workload/run.py --profile normal --rps 250 --duration 60

# Ramp test to detect saturation point
python workload/run.py --profile ramp --rps 500 --duration 90

# Viral product test with custom output path
python workload/run.py --profile viral_product --rps 300 --duration 45 --output data/viral_results.json

# Offline synthetic client simulation (without live services)
python workload/run.py --profile flash_sale --rps 1000 --duration 30 --mock
```

## JSON Summary Output

Every execution generates a standardized JSON summary containing:
- `achieved_rps`: Average requests per second during the test.
- `error_count` & `error_rate_percent`: Total failed requests and percentage.
- `client_side_latency_percentiles`: `p50`, `p90`, `p95`, `p99`, `min`, `max`, `avg` in milliseconds.
- `endpoints`: Granular per-route breakdown (`POST /checkout`, `POST /cart/items`, `GET /products`).
- `metadata`: Seed, target host, execution mode, and UTC timestamps.
