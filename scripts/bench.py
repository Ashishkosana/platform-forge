#!/usr/bin/env python3
"""Measure real throughput and schedule latency. Does not invent numbers."""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx
from psycopg import connect
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[1]


def wait_health(base: str, timeout: float = 15.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = httpx.get(f"{base}/health", timeout=1.0)
            if r.status_code == 200:
                return
        except Exception:
            time.sleep(0.1)
    raise SystemExit("api did not become healthy")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--jobs", type=int, default=50)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--payload-bytes", type=int, default=64)
    parser.add_argument("--workflow", default="noop")
    parser.add_argument("--api-port", type=int, default=43181)
    args = parser.parse_args()

    dsn = os.environ.get("DATABASE_URL", "postgresql://workflow:workflow@127.0.0.1:5432/workflow")
    env = os.environ.copy()
    env["DATABASE_URL"] = dsn
    env["PYTHONPATH"] = str(ROOT / "src")
    env["API_PORT"] = str(args.api_port)
    env["DEMO_MODE"] = "false"
    env["LEASE_TTL_SECONDS"] = "15"
    env["POLL_INTERVAL_SECONDS"] = "0.05"
    env["WORKER_CONCURRENCY"] = "1"

    with connect(dsn, autocommit=True) as conn:
        conn.execute("TRUNCATE lab_effects, attempts, steps, jobs CASCADE")

    api = subprocess.Popen(
        [sys.executable, "-m", "workflow_engine.api"],
        env=env,
        cwd=str(ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    workers = [
        subprocess.Popen(
            [sys.executable, "-m", "workflow_engine.worker"],
            env=env,
            cwd=str(ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(args.workers)
    ]
    base = f"http://127.0.0.1:{args.api_port}"
    try:
        wait_health(base)
        payload = {"blob": "x" * args.payload_bytes}
        t0 = time.perf_counter()
        ids: list[str] = []
        with httpx.Client(base_url=base, timeout=10.0) as client:
            for _i in range(args.jobs):
                res = client.post(
                    "/jobs",
                    json={
                        "workflow_name": args.workflow,
                        "idempotency_key": f"bench-{uuid.uuid4().hex}",
                        "input": payload,
                    },
                )
                res.raise_for_status()
                ids.append(res.json()["id"])
            deadline = time.time() + 60
            while time.time() < deadline:
                statuses = []
                for job_id in ids:
                    statuses.append(client.get(f"/jobs/{job_id}").json()["status"])
                if all(s == "succeeded" for s in statuses):
                    break
                time.sleep(0.05)
            else:
                raise SystemExit("jobs did not complete in time")
        elapsed = time.perf_counter() - t0
        with connect(dsn, row_factory=dict_row) as conn:
            rows = conn.execute(
                """
                SELECT j.created_at, min(a.started_at) AS first_start
                FROM jobs j
                JOIN steps s ON s.job_id = j.id
                JOIN attempts a ON a.step_id = s.id
                WHERE j.idempotency_key LIKE 'bench-%%'
                GROUP BY j.id, j.created_at
                """
            ).fetchall()
        latencies = [(r["first_start"] - r["created_at"]).total_seconds() * 1000 for r in rows]
        latencies.sort()

        def pct(p: float) -> float:
            if not latencies:
                return float("nan")
            idx = min(len(latencies) - 1, max(0, int(round((p / 100) * (len(latencies) - 1)))))
            return latencies[idx]

        result = {
            "measured_at": datetime.now(UTC).isoformat(),
            "environment": {
                "python": platform.python_version(),
                "platform": platform.platform(),
                "machine": platform.machine(),
                "processor": platform.processor(),
                "postgres": "local DATABASE_URL",
            },
            "setup": {
                "jobs": args.jobs,
                "workers": args.workers,
                "workflow": args.workflow,
                "payload_bytes": args.payload_bytes,
                "api_port": args.api_port,
            },
            "results": {
                "wall_seconds": elapsed,
                "jobs_per_second": args.jobs / elapsed if elapsed else None,
                "schedule_latency_ms": {
                    "n": len(latencies),
                    "p50": pct(50),
                    "p95": pct(95),
                    "p99": pct(99),
                    "max": latencies[-1] if latencies else None,
                },
            },
            "limitations": [
                "Single machine, local PostgreSQL.",
                "API process and workers share the host with the bench client.",
                "This is not a production capacity rating.",
            ],
        }
        print(json.dumps(result, indent=2))
    finally:
        for proc in workers:
            proc.terminate()
        api.terminate()
        for proc in [*workers, api]:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


if __name__ == "__main__":
    main()
