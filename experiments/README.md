# Experiments (`experiments/`)

Reproducible parameter exploration schedules, empirical validation runners, and constraint discovery experiments.

## Bottleneck Shifting Experiment (`experiments/bottleneck_shift.py`)

Reproduces the core phenomenon studied in SCOPE-X: as frontend service replicas scale under a fixed downstream database limit, the system bottleneck shifts from CPU-bound to database-pool-bound.

### Experimental Configuration
- **Fixed Parameter**: `DB_POOL_SIZE = 50`
- **Varying Parameter**: Checkout service replicas in `[1, 2, 4, 6, 8]`
- **Workload Profile**: Constant traffic profile (`flash_sale` or `normal`, target RPS: 500)
- **Lifecycle per Run**: Config application -> Warmup phase -> Workload execution -> Telemetry scrape -> Metrics recording

### Running the Experiment

```bash
# Execute standard bottleneck shifting evaluation
python experiments/bottleneck_shift.py

# Custom duration and target RPS
python experiments/bottleneck_shift.py --duration 30 --warmup 5 --rps 500

# Synthetic simulation mode (for headless or offline environments)
python experiments/bottleneck_shift.py --mock
```

### Artifacts Generated

1. **CSV Telemetry Data**: [`data/bottleneck_shift.csv`](file:///Users/aarav/scope-x/data/bottleneck_shift.csv)
   - Columns: `replicas`, `db_pool_size`, `throughput_rps`, `p50_latency_ms`, `p95_latency_ms`, `p99_latency_ms`, `error_count`, `error_rate_pct`, `db_connections`, `cpu_pct`, `timestamp`
2. **Dual-Panel Figure**: [`data/bottleneck_shift.png`](file:///Users/aarav/scope-x/data/bottleneck_shift.png)
   - Panel 1: Throughput (RPS) vs Checkout Replicas with annotated plateau knee
   - Panel 2: Client-side p95 Latency (ms) and Active DB Pool Connections vs Replicas
