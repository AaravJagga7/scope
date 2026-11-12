# Infrastructure (`infra/`)

Kubernetes manifests, Helm chart, and configuration automation targeting **kind / minikube**.

## Structure

```
infra/
├── helm/
│   └── scopex/
│       ├── Chart.yaml             # Helm v2 chart definition
│       ├── values.yaml            # Parameterised configuration defaults
│       └── templates/
│           ├── _helpers.tpl       # Helm naming and label templates
│           ├── namespace.yaml     # scopex namespace definition
│           ├── postgres.yaml      # PostgreSQL Deployment & Service
│           ├── redis.yaml         # Redis Deployment & Service
│           ├── rabbitmq.yaml      # RabbitMQ Broker & Management UI
│           ├── product.yaml       # Product Service (CACHE_ENABLED)
│           ├── cart.yaml          # Cart Service
│           ├── inventory.yaml     # Inventory Service (DB_POOL_SIZE)
│           ├── payment.yaml       # Payment Service (latency / failure rate)
│           ├── order.yaml         # Order Service (DB_POOL_SIZE, consumer count)
│           ├── checkout.yaml      # Checkout Orchestration Service
│           ├── prometheus.yaml    # Prometheus scrape config & Deployment
│           └── grafana.yaml       # Grafana dashboard & datasource provisioning
├── k8s/                           # Standalone declarative Kubernetes manifests
│   ├── kustomization.yaml         # Kustomize manifest bundle
│   ├── namespace.yaml
│   ├── postgres.yaml
│   ├── redis.yaml
│   ├── rabbitmq.yaml
│   ├── product.yaml
│   ├── cart.yaml
│   ├── inventory.yaml
│   ├── payment.yaml
│   ├── order.yaml
│   ├── checkout.yaml
│   ├── hpa-checkout.yaml          # Opt-in HorizontalPodAutoscaler (generated/updated by apply_config)
│   ├── prometheus.yaml
│   └── grafana.yaml
├── prometheus/
│   └── prometheus.yml             # Local Docker Compose scrape configuration
├── grafana/
│   └── provisioning/              # Datasource & Dashboard providers
└── config.example.json            # Reference configuration for experiments
```

## Parameterisations

All operational parameters are dynamically configurable via Helm values (`infra/helm/scopex/values.yaml`) or `scripts/apply_config.py`:

| Parameter | Service Target | Default | Purpose in Constraint Exploration |
|---|---|---|---|
| `replicas` | All 6 services | `2` (Checkout: 2) | Horizontal pod scaling |
| `resources.requests/limits` | All services | `100m / 500m`, `128Mi / 512Mi` | CPU & memory throttling |
| `DB_POOL_SIZE` | `order`, `inventory` | `10` | Database connection saturation limit |
| `CACHE_ENABLED` | `product` | `true` | Cache hit vs DB bottleneck analysis |
| `ORDER_CONSUMER_CONCURRENCY` | `order` | `1` | Asynchronous queue drain capacity |

## Applying Configurations with `scripts/apply_config.py`

Apply any experiment configuration JSON to the cluster:

```bash
# Apply configuration and verify pod rollout
python scripts/apply_config.py config.json

# Dry-run mode: inspect changes without modifying cluster state
python scripts/apply_config.py config.json --dry-run

# Custom timeout for rollout readiness (seconds)
python scripts/apply_config.py config.json --timeout 120
```

When connected to a live Kubernetes cluster (`kind` or `minikube`), the script triggers `kubectl scale`, `kubectl set env`, and tracks `kubectl rollout status`. In development environments without a live cluster, the script manages the local cluster state simulation, manifest updates, and verifies pod readiness.

## Scaling Modes (Task 14A)

Every `ExperimentConfig` carries a `scaling_mode` of `static`, `hpa`, or `vertical`. The cluster controller exposes the same modes:

```bash
# Horizontal: enable HPA on Checkout (CPU metric, parameterised behaviour)
python scripts/apply_config.py config.json --enable-hpa \
    --hpa-min 2 --hpa-max 8 --hpa-metric cpu --hpa-target-cpu 70 \
    --hpa-scale-up-window 0 --hpa-scale-down-window 60

# Disable HPA and return Checkout to static replica control
python scripts/apply_config.py config.json --disable-hpa

# Vertical: select a defined resource level (request = 50% of limit by default)
python scripts/apply_config.py config.json --vpa-level 2 --no-in-place-resize

# Vertical: explicit requests/limits, recommendation-only (never applied)
python scripts/apply_config.py config.json --vpa-mode recommendation \
    --vpa-cpu-request 500m --vpa-cpu-limit 1000m \
    --vpa-memory-request 512Mi --vpa-memory-limit 1024Mi
```

- **HPA** rewrites `infra/k8s/hpa-checkout.yaml` with the requested metric, target utilisation, replica bounds, and scale-up/scale-down stabilisation windows and policies.
- **Vertical** rewrites Checkout's CPU/memory requests and limits. In-place resize is used when the API server exposes the `pods/resize` subresource (`InPlacePodVerticalScaling`); otherwise the controller falls back to a rolling restart and records the restart window.
- **Safety invariant:** HPA and VPA may never drive the *same* metric. Configurations that would do so are rejected with a `Safety Violation` error before any manifest or cluster state is touched.
- Per-run telemetry (replica-count series, CPU/memory requests and limits, pod start/ready times, and timestamped scaling events) is stored with each run in SQLite/Parquet.
