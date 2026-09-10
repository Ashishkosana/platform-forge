from __future__ import annotations

import os
import time
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def wait_until(
    predicate: Callable[[], bool], timeout: float = 20.0, interval: float = 0.05
) -> None:
    deadline = time.time() + timeout
    last: Exception | None = None
    while time.time() < deadline:
        try:
            if predicate():
                return
        except Exception as exc:  # noqa: BLE001
            last = exc
        time.sleep(interval)
    raise AssertionError(f"timeout waiting for condition: {last}")


def src_env(database_url: str) -> dict[str, str]:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["PYTHONPATH"] = str(ROOT / "src")
    env["LEASE_TTL_SECONDS"] = os.environ.get("LEASE_TTL_SECONDS", "2")
    env["HEARTBEAT_INTERVAL_SECONDS"] = os.environ.get("HEARTBEAT_INTERVAL_SECONDS", "0.4")
    env["POLL_INTERVAL_SECONDS"] = os.environ.get("POLL_INTERVAL_SECONDS", "0.05")
    env["WORKER_CONCURRENCY"] = "1"
    env["BASE_BACKOFF_SECONDS"] = os.environ.get("BASE_BACKOFF_SECONDS", "0.05")
    env["MAX_BACKOFF_SECONDS"] = os.environ.get("MAX_BACKOFF_SECONDS", "0.4")
    env["LOG_LEVEL"] = "INFO"
    return env
