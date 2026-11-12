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
