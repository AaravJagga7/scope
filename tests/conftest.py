"""Pytest configuration and environment fixtures for SCOPE-X."""

import os
from pathlib import Path

# Prevent Locust from monkey-patching standard library during pytest runs
os.environ["LOCUST_SKIP_MONKEY_PATCH"] = "1"

# Ensure testing uses an isolated SQLite database and in-memory cache
TEST_DB_PATH = Path(__file__).resolve().parent / "test_scopex.db"
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB_PATH}"
os.environ["CACHE_ENABLED"] = "false"
os.environ["PAYMENT_FAILURE_RATE"] = "0.0"
os.environ["PAYMENT_LATENCY_MS"] = "0"


def pytest_sessionfinish(session, exitstatus) -> None:
    """Clean up test SQLite database after test run."""
    if TEST_DB_PATH.exists():
        TEST_DB_PATH.unlink(missing_ok=True)
