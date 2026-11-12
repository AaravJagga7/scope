# Scale Manager (Person 4, Phase 2)

Changes Kubernetes capacity under program control and records every change.
It needs only the Python standard library and `kubectl` on PATH; it uses whatever
cluster `kubectl` points at (Minikube by default).

| File | Purpose |
|---|---|
| `config.py` | namespace, services, replica bounds (1–6, same as the OR engine), timeout, log path |
| `status.py` | reads deployment + pod state via `kubectl ... -o json` |
| `scale.py` | `ScaleManager`: scale, wait until settled, set resources, apply S, append to log |
| `../kubernetes/checkout-*.yaml` | Checkout Deployment (nginx placeholder) + Service |
| `../tests/test_scale_manager.py` | 8 tests against a fake kubectl |

**"Settled"** means: the controller has seen the new spec, exactly N pods are
ready, available and up to date, and no pods are still terminating. Scale-down
times therefore include connection draining.

```
python -m scale_manager.status
python -m scale_manager.scale --replicas 4 --note "first 2->4 test"
python -m scale_manager.scale --sequence 2 4 6 4 2 --hold 5
python -m scale_manager.scale --show-log
```

From Python (this is what the experiment runner will call):

```python
from scale_manager.scale import ScaleManager
sm = ScaleManager()
ev = sm.scale(4, note="exp 17")          # blocks until settled; ev.ready_s, ev.success
sm.apply_scale_config({"checkout_replicas": 4, "checkout_cpu": "500m", "checkout_memory": "512Mi"})
```

Log: `datasets/infra/scale_events.csv` (append-only; one row per change, including failures).

To add Inventory later: add `"inventory": ServiceTarget("inventory", "app=inventory")`
to `services` in `config.py`; `inventory_replicas` then works in `apply_scale_config`.
