"""Local testbed manager: runs the six SCOPE-X microservices with a chosen config.

Every ``apply`` is a reset to a known baseline: services are stopped, the SQLite
database is deleted, and all services are relaunched with the new settings. The
start and ready timestamps of every service are recorded.

Horizontal scaling is real here: checkout and inventory can run several uvicorn
worker processes. Cart keeps its state in process memory, so it always runs one
worker. Without Docker there is no Postgres or Redis: inventory and order use
SQLite, and the cache falls back to the in-memory store.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import httpx

from storefront.catalog import inventory_seed

BASE_DIR = Path(__file__).resolve().parent.parent
RUN_DIR = BASE_DIR / ".storefront"
DB_FILE = RUN_DIR / "testbed.db"
LOG_FILE = RUN_DIR / "services.log"
LEGACY_PID_FILE = BASE_DIR / ".services.pid"

PORTS = {
    "product": 8001,
    "cart": 8002,
    "inventory": 8003,
    "payment": 8004,
    "order": 8005,
    "checkout": 8006,
}
URLS = {name: f"http://127.0.0.1:{port}" for name, port in PORTS.items()}


@dataclass
class TestbedConfig:
    """Knobs the Constraint Lab can change on the live local testbed."""

    __test__ = False  # not a pytest test class despite the name

    checkout_workers: int = 1
    inventory_workers: int = 1
    payment_latency_ms: float = 50.0
    payment_failure_rate: float = 0.0
    order_delay_ms: float = 50.0
    cache_enabled: bool = True
    db_pool_size: int = 10

    def validate(self) -> None:
        if not 1 <= self.checkout_workers <= 8:
            raise ValueError("checkout_workers must be between 1 and 8")
        if not 1 <= self.inventory_workers <= 4:
            raise ValueError("inventory_workers must be between 1 and 4")
        if not 0 <= self.payment_latency_ms <= 5000:
            raise ValueError("payment_latency_ms must be between 0 and 5000")
        if not 0.0 <= self.payment_failure_rate <= 1.0:
            raise ValueError("payment_failure_rate must be between 0 and 1")
        if not 0 <= self.order_delay_ms <= 5000:
            raise ValueError("order_delay_ms must be between 0 and 5000")
        if not 1 <= self.db_pool_size <= 200:
            raise ValueError("db_pool_size must be between 1 and 200")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TestbedConfig:
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        cfg = cls(**known)
        cfg.checkout_workers = int(cfg.checkout_workers)
        cfg.inventory_workers = int(cfg.inventory_workers)
        cfg.db_pool_size = int(cfg.db_pool_size)
        cfg.payment_latency_ms = float(cfg.payment_latency_ms)
        cfg.payment_failure_rate = float(cfg.payment_failure_rate)
        cfg.order_delay_ms = float(cfg.order_delay_ms)
        cfg.cache_enabled = bool(cfg.cache_enabled)
        cfg.validate()
        return cfg


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _python() -> str:
    venv = BASE_DIR / ".venv" / "bin" / "python"
    return str(venv) if venv.exists() else sys.executable


class Testbed:
    """Starts, stops and reconfigures the local microservice processes."""

    def __init__(self) -> None:
        self._procs: dict[str, subprocess.Popen] = {}
        self._lock = threading.Lock()
        self.config = TestbedConfig()
        self.state = "stopped"
        self.last_error = ""
        self.timeline: list[dict[str, Any]] = []
        self.applied_at: float | None = None

    # -- lifecycle ---------------------------------------------------------

    def _env(self, cfg: TestbedConfig) -> dict[str, str]:
        env = os.environ.copy()
        env.update(
            {
                "PYTHONPATH": str(BASE_DIR),
                "DATABASE_URL": f"sqlite:///{DB_FILE}",
                "DB_POOL_SIZE": str(cfg.db_pool_size),
                "CACHE_ENABLED": "true" if cfg.cache_enabled else "false",
                "PAYMENT_LATENCY_MS": str(cfg.payment_latency_ms),
                "PAYMENT_FAILURE_RATE": str(cfg.payment_failure_rate),
                "ORDER_PROCESSING_DELAY_MS": str(cfg.order_delay_ms),
                "CART_SERVICE_URL": URLS["cart"],
                "INVENTORY_SERVICE_URL": URLS["inventory"],
                "PAYMENT_SERVICE_URL": URLS["payment"],
                "ORDER_SERVICE_URL": URLS["order"],
                "PRODUCT_SERVICE_URL": URLS["product"],
                "INVENTORY_SEED_JSON": json.dumps(inventory_seed()),
                # Lets the drop test reset the drop item's stock to a known baseline.
                "INVENTORY_ADMIN_ENABLED": "true",
                "LOG_LEVEL": "WARNING",
                # Tracing exporters are not available locally.
                "OTEL_TRACES_EXPORTER": "none",
            }
        )
        return env

    def _workers(self, name: str, cfg: TestbedConfig) -> int:
        if name == "checkout":
            return cfg.checkout_workers
        if name == "inventory":
            return cfg.inventory_workers
        return 1

    def _stop_legacy(self) -> None:
        """Stops services started by scripts/local_dev_server.py so ports are free."""
        if not LEGACY_PID_FILE.exists():
            return
        for line in LEGACY_PID_FILE.read_text().splitlines():
            try:
                os.killpg(int(line.split(":")[0]), signal.SIGTERM)
            except (ProcessLookupError, ValueError, PermissionError):
                pass
        LEGACY_PID_FILE.unlink(missing_ok=True)

    def stop(self) -> None:
        with self._lock:
            self._stop_locked()

    def _stop_locked(self) -> None:
        for proc in self._procs.values():
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
        deadline = time.time() + 8
        for proc in self._procs.values():
            try:
                proc.wait(timeout=max(0.1, deadline - time.time()))
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        self._procs.clear()
        self.state = "stopped"

    def apply(self, cfg: TestbedConfig, timeout: float = 45.0) -> dict[str, Any]:
        """Resets to baseline and relaunches every service with *cfg*."""
        cfg.validate()
        with self._lock:
            self.state = "starting"
            self.last_error = ""
            self._stop_locked()
            self._stop_legacy()
            busy = [n for n, p in PORTS.items() if _port_in_use(p)]
            if busy:
                time.sleep(1.5)
                busy = [n for n, p in PORTS.items() if _port_in_use(p)]
            if busy:
                self.state = "error"
                self.last_error = (
                    f"Ports in use by another process: {', '.join(f'{n}:{PORTS[n]}' for n in busy)}"
                )
                return self.status()

            RUN_DIR.mkdir(exist_ok=True)
            DB_FILE.unlink(missing_ok=True)
            env = self._env(cfg)
            log_fp = open(LOG_FILE, "a")  # noqa: SIM115 - handle owned by child processes
            timeline: list[dict[str, Any]] = []
            # Inventory and order create the SQLite schema; start them first.
            order = ["inventory", "order", "product", "cart", "payment", "checkout"]
            for name in order:
                cmd = [
                    _python(),
                    "-m",
                    "uvicorn",
                    f"services.{name}.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(PORTS[name]),
                    "--log-level",
                    "warning",
                    "--workers",
                    str(self._workers(name, cfg)),
                ]
                started = time.time()
                self._procs[name] = subprocess.Popen(
                    cmd,
                    cwd=str(BASE_DIR),
                    env=env,
                    stdout=log_fp,
                    stderr=log_fp,
                    start_new_session=True,
                )
                timeline.append(
                    {
                        "service": name,
                        "workers": self._workers(name, cfg),
                        "start_ts": started,
                        "ready_ts": None,
                    }
                )
                if name == "inventory":
                    self._wait_ready([timeline[-1]], timeout)

            self._wait_ready(timeline, timeout)
            self.timeline = timeline
            self.config = cfg
            self.applied_at = time.time()
            not_ready = [t["service"] for t in timeline if t["ready_ts"] is None]
            if not_ready:
                self.state = "error"
                self.last_error = f"Not ready after {timeout:.0f}s: {', '.join(not_ready)}"
            else:
                self.state = "running"
            return self.status()

    def _wait_ready(self, entries: list[dict[str, Any]], timeout: float) -> None:
        deadline = time.time() + timeout
        pending = [e for e in entries if e["ready_ts"] is None]
        while pending and time.time() < deadline:
            for entry in list(pending):
                proc = self._procs.get(entry["service"])
                if proc is not None and proc.poll() is not None:
                    pending.remove(entry)  # crashed; leave ready_ts None
                    continue
                try:
                    resp = httpx.get(f"{URLS[entry['service']]}/health", timeout=0.5)
                    if resp.status_code == 200:
                        entry["ready_ts"] = time.time()
                        pending.remove(entry)
                except httpx.HTTPError:
                    pass
            if pending:
                time.sleep(0.2)

    def status(self) -> dict[str, Any]:
        services = []
        for name, port in PORTS.items():
            proc = self._procs.get(name)
            alive = proc is not None and proc.poll() is None
            entry = next((t for t in self.timeline if t["service"] == name), {})
            ready = entry.get("ready_ts")
            start = entry.get("start_ts")
            services.append(
                {
                    "service": name,
                    "port": port,
                    "alive": alive,
                    "workers": self._workers(name, self.config),
                    "ready_seconds": round(ready - start, 2) if ready and start else None,
                }
            )
        if self.state == "running" and not all(s["alive"] for s in services):
            self.state = "degraded"
        return {
            "state": self.state,
            "error": self.last_error,
            "config": asdict(self.config),
            "applied_at": self.applied_at,
            "services": services,
            "storage": "SQLite (no Postgres locally); cache uses in-memory fallback (no Redis)",
        }
