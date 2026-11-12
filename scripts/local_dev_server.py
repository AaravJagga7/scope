#!/usr/bin/env python3
"""Local multi-service process manager for development and testing when Docker is unavailable."""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
PID_FILE = BASE_DIR / ".services.pid"
LOG_FILE = BASE_DIR / ".services.log"
DB_FILE = BASE_DIR / "scopex_dev.db"

SERVICES = [
    ("product", "services.product.main:app", 8001),
    ("cart", "services.cart.main:app", 8002),
    ("inventory", "services.inventory.main:app", 8003),
    ("payment", "services.payment.main:app", 8004),
    ("order", "services.order.main:app", 8005),
    ("checkout", "services.checkout.main:app", 8006),
]


def get_python_exec() -> str:
    venv_py = BASE_DIR / ".venv" / "bin" / "python"
    if venv_py.exists():
        return str(venv_py)
    return sys.executable


def start_services() -> None:
    if PID_FILE.exists():
        # verify if actually running
        stop_services()

    pids = []
    env = os.environ.copy()
    env["PYTHONPATH"] = str(BASE_DIR)

    # Use SQLite for local development fallback if PostgreSQL is not specified
    if "DATABASE_URL" not in env:
        env["DATABASE_URL"] = f"sqlite:///{DB_FILE}"
    env.setdefault("DB_POOL_SIZE", "10")
    env.setdefault("CACHE_ENABLED", "false")
    env.setdefault("CART_SERVICE_URL", "http://127.0.0.1:8002")
    env.setdefault("INVENTORY_SERVICE_URL", "http://127.0.0.1:8003")
    env.setdefault("PAYMENT_SERVICE_URL", "http://127.0.0.1:8004")
    env.setdefault("ORDER_SERVICE_URL", "http://127.0.0.1:8005")

    python_exec = get_python_exec()
    log_fp = open(LOG_FILE, "a")

    print(f"Starting SCOPE-X microservices with {python_exec}...")
    for name, app_target, port in SERVICES:
        cmd = [
            python_exec,
            "-m",
            "uvicorn",
            app_target,
            "--host",
            "0.0.0.0",
            "--port",
            str(port),
            "--log-level",
            "info",
        ]
        proc = subprocess.Popen(
            cmd,
            cwd=str(BASE_DIR),
            env=env,
            stdout=log_fp,
            stderr=log_fp,
            start_new_session=True,
        )
        pids.append(f"{proc.pid}:{name}:{port}")
        print(f"  ✓ {name:10} (PID {proc.pid}) running on http://127.0.0.1:{port}")

    PID_FILE.write_text("\n".join(pids))
    print("\nWaiting for services to become healthy...")
    time.sleep(2)
    print("All services launched. PID file written to .services.pid")


def stop_services() -> None:
    if not PID_FILE.exists():
        print("No active PID file found. Services may not be running.")
        return

    lines = PID_FILE.read_text().strip().splitlines()
    for line in lines:
        if not line.strip():
            continue
        parts = line.split(":")
        pid = int(parts[0])
        name = parts[1] if len(parts) > 1 else "service"
        try:
            os.kill(pid, signal.SIGTERM)
            print(f"  ✓ Stopped {name} (PID {pid})")
        except ProcessLookupError:
            pass
        except Exception as e:
            print(f"  ! Could not kill {name} (PID {pid}): {e}")

    PID_FILE.unlink(missing_ok=True)
    if DB_FILE.exists():
        DB_FILE.unlink(missing_ok=True)
    print("Services stopped and temporary database cleaned.")


def status_services() -> None:
    if not PID_FILE.exists():
        print("Services are stopped (no .services.pid file).")
        return

    lines = PID_FILE.read_text().strip().splitlines()
    for line in lines:
        if not line.strip():
            continue
        pid, name, port = line.split(":")
        try:
            os.kill(int(pid), 0)
            status_text = "RUNNING"
        except (ProcessLookupError, OSError):
            status_text = "DEAD"
        print(f"  {name:10} (port {port}, PID {pid}): {status_text}")


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "status"
    if action == "start":
        start_services()
    elif action == "stop":
        stop_services()
    elif action == "status":
        status_services()
    else:
        print("Usage: local_dev_server.py [start|stop|status]")
