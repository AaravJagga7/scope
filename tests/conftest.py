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


def _remove_sqlite_files(db_path: Path) -> None:
    """Remove a SQLite DB and any WAL / shared-memory sidecar files.

    Stale -wal / -shm files left over from a previous aborted test run can
    cause SQLite to open the database in read-only mode on the next run.
    """
    for suffix in ("", "-wal", "-shm"):
        p = db_path.with_name(db_path.name + suffix)
        p.unlink(missing_ok=True)


# Clean up any leftover DB from a prior (possibly crashed) run *before* the
# service modules are imported, so they always start with a fresh file.
_remove_sqlite_files(TEST_DB_PATH)


def pytest_sessionfinish(session, exitstatus) -> None:
    """Clean up test SQLite database after test run."""
    _remove_sqlite_files(TEST_DB_PATH)
