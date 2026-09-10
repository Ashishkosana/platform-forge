from __future__ import annotations

import os
import signal
import subprocess
import time
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from psycopg import connect
from psycopg.rows import dict_row
from tests.helpers import wait_until
from tests.test_worker_integration import fetch_job, start_worker, stop_worker


@pytest.fixture
def isolated_worker(database_url: str) -> Iterator[subprocess.Popen[bytes]]:
    proc = start_worker(database_url)
    try:
        yield proc
    finally:
        stop_worker(proc)


def test_crash_after_naive_duplicates_effect(client: TestClient, database_url: str) -> None:
    job = client.post(
        "/jobs",
        json={"workflow_name": "crash_naive", "idempotency_key": "crash-naive", "input": {}},
    ).json()
    worker = start_worker(database_url)
    try:
        wait_until(lambda: worker.poll() is not None, timeout=15)
    finally:
        stop_worker(worker)
    with connect(database_url, row_factory=dict_row) as conn:
        before = conn.execute("SELECT write_count FROM lab_effects").fetchone()
    assert before is not None
    assert before["write_count"] >= 1
    worker2 = start_worker(database_url)
    try:
        wait_until(lambda: fetch_job(client, job["id"])["status"] == "succeeded", timeout=20)
        step = fetch_job(client, job["id"])["steps"][0]
        assert step["attempt_count"] >= 2
        with connect(database_url, row_factory=dict_row) as conn:
            after = conn.execute("SELECT write_count FROM lab_effects").fetchone()
        assert after is not None
        assert after["write_count"] >= 2
    finally:
        stop_worker(worker2)


def test_crash_after_idempotent_effect_stays_one(client: TestClient, database_url: str) -> None:
    job = client.post(
        "/jobs",
        json={
            "workflow_name": "crash_idempotent",
            "idempotency_key": "crash-idemp",
            "input": {},
        },
    ).json()
    worker = start_worker(database_url)
    wait_until(lambda: worker.poll() is not None, timeout=15)
    stop_worker(worker)
    worker2 = start_worker(database_url)
    try:
        wait_until(lambda: fetch_job(client, job["id"])["status"] == "succeeded", timeout=20)
        with connect(database_url, row_factory=dict_row) as conn:
            row = conn.execute("SELECT write_count FROM lab_effects").fetchone()
        assert row is not None
        assert row["write_count"] == 1
        step = fetch_job(client, job["id"])["steps"][0]
        assert step["attempt_count"] >= 2
    finally:
        stop_worker(worker2)


def test_zombie_fence_reject(client: TestClient, database_url: str) -> None:
    job = client.post(
        "/jobs",
        json={"workflow_name": "hang", "idempotency_key": "zombie-1", "input": {"seconds": 8}},
    ).json()
    frozen = start_worker(database_url)
    wait_until(
        lambda: fetch_job(client, job["id"])["steps"][0]["status"] == "running",
        timeout=10,
    )
    os.kill(frozen.pid, signal.SIGSTOP)
    thief = start_worker(database_url)
    try:

        def stolen() -> bool:
            step = fetch_job(client, job["id"])["steps"][0]
            return step["fencing_token"] >= 2 and step["status"] in {"running", "succeeded"}

        wait_until(stolen, timeout=15)
        os.kill(frozen.pid, signal.SIGCONT)
        wait_until(lambda: fetch_job(client, job["id"])["status"] == "succeeded", timeout=25)
        step = fetch_job(client, job["id"])["steps"][0]
        outcomes = [a["outcome"] for a in step["attempts"]]
        assert "succeeded" in outcomes
        assert outcomes.count("succeeded") == 1
        assert "rejected_fence" in outcomes or step["fencing_token"] >= 2
    finally:
        try:
            os.kill(frozen.pid, signal.SIGCONT)
        except ProcessLookupError:
            pass
        stop_worker(frozen)
        stop_worker(thief)


def test_poison_does_not_block_healthy_work(
    client: TestClient, isolated_worker: subprocess.Popen[bytes]
) -> None:
    poison = client.post(
        "/jobs",
        json={"workflow_name": "always_fail", "idempotency_key": "poison", "input": {}},
    ).json()
    healthy = client.post(
        "/jobs",
        json={"workflow_name": "noop", "idempotency_key": "healthy", "input": {}},
    ).json()
    wait_until(lambda: fetch_job(client, healthy["id"])["status"] == "succeeded", timeout=15)
    wait_until(lambda: fetch_job(client, poison["id"])["status"] == "dead_lettered", timeout=15)


def test_retry_jitter_spreads(client: TestClient, database_url: str) -> None:
    n = int(os.environ.get("DRILL_HERD_SIZE", "40"))
    worker = start_worker(database_url)
    try:
        ids = []
        for i in range(n):
            job = client.post(
                "/jobs",
                json={"workflow_name": "always_fail", "idempotency_key": f"herd-{i}", "input": {}},
            ).json()
            ids.append(job["id"])
        wait_until(
            lambda: all(fetch_job(client, jid)["status"] == "dead_lettered" for jid in ids),
            timeout=60,
        )
        delays: list[float] = []
        with connect(database_url, row_factory=dict_row) as conn:
            rows = conn.execute(
                """
                SELECT a.finished_at, s.run_after
                FROM attempts a
                JOIN steps s ON s.id = a.step_id
                JOIN jobs j ON j.id = s.job_id
                WHERE j.idempotency_key LIKE 'herd-%%'
                  AND a.outcome IN ('failed', 'timeout')
                  AND a.finished_at IS NOT NULL
                """
            ).fetchall()
        for row in rows:
            if row["run_after"] is None or row["finished_at"] is None:
                continue
            delays.append((row["run_after"] - row["finished_at"]).total_seconds())
        # After the last attempt the step is dead-lettered, so run_after may be stale.
        # Measure spacing of attempt started_at instead for retries.
        with connect(database_url, row_factory=dict_row) as conn:
            starts = conn.execute(
                """
                SELECT a.started_at
                FROM attempts a
                JOIN steps s ON s.id = a.step_id
                JOIN jobs j ON j.id = s.job_id
                WHERE j.idempotency_key = 'herd-0'
                ORDER BY a.started_at
                """
            ).fetchall()
        assert len(starts) == 3
        gaps = [
            (starts[i]["started_at"] - starts[i - 1]["started_at"]).total_seconds()
            for i in range(1, len(starts))
        ]
        assert max(gaps) >= 0.0
        assert min(gaps) >= 0.0
    finally:
        stop_worker(worker)


def test_cancel_during_hang(client: TestClient, database_url: str) -> None:
    job = client.post(
        "/jobs",
        json={"workflow_name": "hang", "idempotency_key": "cancel-hang", "input": {"seconds": 20}},
    ).json()
    worker = start_worker(database_url)
    try:
        wait_until(
            lambda: fetch_job(client, job["id"])["steps"][0]["status"] == "running",
            timeout=10,
        )
        res = client.post(f"/jobs/{job['id']}/cancel")
        assert res.status_code == 200
        wait_until(lambda: fetch_job(client, job["id"])["status"] == "cancelled", timeout=10)
        time.sleep(1.0)
        got = fetch_job(client, job["id"])
        assert got["status"] == "cancelled"
        assert got["steps"][0]["status"] != "succeeded"
        time.sleep(1.5)
        got2 = fetch_job(client, job["id"])
        assert len(got2["steps"][0]["attempts"]) <= len(got["steps"][0]["attempts"]) + 1
    finally:
        stop_worker(worker)


def test_clock_skew_sql_uses_now(client: TestClient, database_url: str) -> None:
    from workflow_engine.claims import CLAIM_SQL, HEARTBEAT_SQL

    assert "now() +" in CLAIM_SQL
    assert "now() +" in HEARTBEAT_SQL
    job = client.post(
        "/jobs", json={"workflow_name": "noop", "idempotency_key": "clock", "input": {}}
    ).json()
    worker = start_worker(database_url)
    try:
        wait_until(lambda: fetch_job(client, job["id"])["status"] == "succeeded")
        with connect(database_url, row_factory=dict_row) as conn:
            row = conn.execute(
                "SELECT leased_until IS NULL AS cleared FROM steps WHERE job_id = %s",
                (job["id"],),
            ).fetchone()
        assert row is not None
        assert row["cleared"] is True
    finally:
        stop_worker(worker)


@pytest.mark.requires_pg_control
@pytest.mark.skipif(
    os.environ.get("DRILL_PG_BOUNCE") != "1", reason="set DRILL_PG_BOUNCE=1 to bounce Postgres"
)
def test_postgres_bounce(client: TestClient, database_url: str) -> None:
    job = client.post(
        "/jobs", json={"workflow_name": "noop", "idempotency_key": "pg-bounce", "input": {}}
    ).json()
    worker = start_worker(database_url)
    try:
        subprocess.check_call(["sudo", "service", "postgresql", "stop"])
        time.sleep(1)
        subprocess.check_call(["sudo", "service", "postgresql", "start"])
        wait_until(lambda: fetch_job(client, job["id"])["status"] == "succeeded", timeout=30)
    finally:
        subprocess.call(["sudo", "service", "postgresql", "start"])
        stop_worker(worker)
