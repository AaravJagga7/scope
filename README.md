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

