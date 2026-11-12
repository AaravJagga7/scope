# SCOPE-X

SCOPE-X (Scalable Constraint Optimization & Parameter Exploration for e-commerce on Kubernetes) is a research platform designed to discover, verify, and transfer operational constraints across infrastructure scale changes in microservice architectures.

## System Overview

The experimental testbed simulates a Shopify-inspired microservice architecture (Product, Cart, Checkout, Payment, Inventory, and Order services with PostgreSQL, Redis, and message queues) deployed on Kubernetes. SCOPE-X addresses the research question:
*Can we discover and update accurate operational constraints with fewer experiments than rediscovering them after every infrastructure change?*

## Repository Structure

```
scope-x/
├── dashboard/       # Monitoring dashboards and visualization definitions (e.g., Grafana)
├── data/            # Experiment artifacts, telemetry logs, and benchmark datasets
├── docs/            # Research documentation, formal specifications, and architecture notes
├── experiments/     # Reproducible experiment configurations, runners, and execution logs
├── infra/           # Infrastructure manifests and Kubernetes configurations (kind/minikube/Helm)
├── scopex/          # Core Python research package
│   ├── decision/    # Policy decision engines and adaptation logic across scale boundaries
│   ├── discovery/   # Active learning and ML-driven operational constraint discovery
│   ├── optimisation/# Multi-objective optimization (BoTorch/GPyTorch, OR-Tools CP-SAT)
│   ├── storage/     # Reproducible storage and metadata tracking for experiment runs
│   └── verification/# Formal verification and constraint validation models (Z3 SMT solver)
├── services/        # Microservice source code (Product, Cart, Checkout, Payment, Inventory, Order)
├── tests/           # Unit, integration, and property-based test suites
├── workload/        # Synthetic workload definitions and load profiles (Locust scenarios)
├── Makefile         # Common build, deployment, test, and lint commands
└── pyproject.toml   # Project metadata, dependencies, and tooling configuration
```

## Quickstart

### Prerequisites
- Python 3.11+
- Make

### Setup and Development
```bash
# Setup virtual environment and dependencies
make setup

# Run linter
make lint

# Run test suite
make test
```


## Bazaaro storefront + Constraint Lab

An original demo marketplace (`storefront/`) running on the SCOPE-X microservices, with a lab for testing how the operational constraints change.

```bash
make store            # or: python -m storefront --port 8010
# open http://127.0.0.1:8010
```

The store starts the six microservices itself (ports 8001-8006). Every order goes through the real cart → inventory → payment → order flow. The header health indicator and the banner come from the store's own live service latencies.

The **Constraint Lab** (`#/lab`) has four tabs:

- **Live tests** (*validated on local testbed*): change checkout/inventory workers, payment latency and failure rate, and the cache, then **Apply & reset baseline**. Run a boundary sweep, a flash sale or a steady-load test. Each sweep reports the observed SLO bound, its tested region, and whether the load generator kept up. A history table shows how the bound shifts across configurations.
- **Model what-if** (*model, simulated*): move replicas, CPU, memory, pool, cache and load. See predicted p95, the bottleneck, how the SLO boundary moves with replicas and CPU, and which stored constraints fire, with their validity in static, HPA and vertical modes.
- **Constraint repository**: every stored constraint with its class per scaling mode (from `data/*_summary.json`).
- **Flash-sale shield**: switch the shield's layers and thresholds live, watch the queue, and run the **drop test**. Simulated humans and bots buy the 50-unit limited item (`bz-701`) through the store's API, once with the shield off and once with it on. Results are saved to `data/experiments/drop_test_*.json` with params, seed, timestamps and git commit.

### Flash-sale shield

The shield guards add-to-cart and checkout; browsing stays open. It is on by default (`SHIELD_ENABLED=0` turns it off).

- **Waiting room** (`storefront/shield.py`): opens when purchase-path traffic or the number of active shoppers passes a threshold, or always in drop mode. Shoppers get a FIFO position and ETA and are admitted at a fixed rate per minute.
- **Stock reservation with TTL** (`services/inventory`): add-to-cart creates a hold (default 5 min) using a single conditional `UPDATE … WHERE available_qty >= q`, so concurrent buyers cannot oversell. Unpaid holds expire and return to stock. Checkout locks and commits the hold. Limited items have a per-customer limit.
- **Bot defence**: per-IP and per-device token-bucket rate limits; an explainable 0–100 behaviour score (automation user-agent, no product view, too fast, cookie dropping, no interaction signals, failed challenges); SHA-256 proof-of-work for suspicious sessions and for joining the queue. There is no CAPTCHA provider locally.

Measured locally (50 units, 200 humans, 10 bots × 8/s, 25% abandon, 20 s hold TTL; *validated on local testbed*):

| | shield off | shield on |
|---|---|---|
| units sold / stock | 74–104 / 50 (3 runs) | 34 / 50 |
| oversold | 24–54 | 0 |
| units bought by bots | 100% | 0% |
| human checkout p95 | (no human got one) | 91–93 ms |

With the shield off, the legacy `/inventory/reserve` read-then-write races on SQLite, which causes the oversell. With the shield on, 16 abandoned holds expired back into stock after every remaining human had already been told "sold out", so they went unsold.

Local caveats: no Docker means SQLite instead of Postgres and an in-memory cache instead of Redis. "Replicas" are uvicorn worker processes on one machine, and the load generator shares that machine. The SLO thresholds (p95 ≤ 500 ms, errors ≤ 1%) are configurable in the Lab.
