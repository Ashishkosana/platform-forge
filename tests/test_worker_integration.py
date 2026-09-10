from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from psycopg import connect
from psycopg.rows import dict_row

from tests.helpers import src_env, wait_until


def start_worker(database_url: str, extra: dict[str, str] | None = None) -> subprocess.Popen[bytes]:
    env = src_env(database_url)
    if extra:
        env.update(extra)
    return subprocess.Popen(
        [sys.executable, "-m", "workflow_engine.worker"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def stop_worker(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.fixture
def worker(database_url: str) -> Iterator[subprocess.Popen[bytes]]:
    proc = start_worker(database_url)
    try:
        yield proc
    finally:
        stop_worker(proc)


def fetch_job(client: TestClient, job_id: str) -> dict[str, Any]:
    res = client.get(f"/jobs/{job_id}")
    assert res.status_code == 200
    return res.json()


def test_noop_completes(client: TestClient, worker: subprocess.Popen[bytes]) -> None:
    job = client.post(
        "/jobs", json={"workflow_name": "noop", "idempotency_key": "w-noop", "input": {}}
    ).json()
    wait_until(lambda: fetch_job(client, job["id"])["status"] == "succeeded")
    got = fetch_job(client, job["id"])
    assert got["steps"][0]["status"] == "succeeded"
    assert got["steps"][0]["attempts"][0]["outcome"] == "succeeded"


def test_linear_demo_unblocks_in_order(client: TestClient, worker: subprocess.Popen[bytes]) -> None:
    job = client.post(
        "/jobs",
        json={"workflow_name": "linear_demo", "idempotency_key": "lin-1", "input": {}},
    ).json()
    wait_until(lambda: fetch_job(client, job["id"])["status"] == "succeeded")
    steps = fetch_job(client, job["id"])["steps"]
    assert [s["name"] for s in steps] == ["charge", "notify", "provision"]
    assert all(s["status"] == "succeeded" for s in steps)
    assert steps[0]["seq"] == 1


def test_fail_until_retries(client: TestClient, worker: subprocess.Popen[bytes]) -> None:
    job = client.post(
        "/jobs",
        json={
            "workflow_name": "fail_until",
            "idempotency_key": "fu-1",
            "input": {"fail_until": 3},
        },
    ).json()
    wait_until(lambda: fetch_job(client, job["id"])["status"] == "succeeded", timeout=15)
    step = fetch_job(client, job["id"])["steps"][0]
    assert step["attempt_count"] == 3
    outcomes = [a["outcome"] for a in step["attempts"]]
    assert outcomes.count("failed") == 2
    assert outcomes[-1] == "succeeded"


def test_always_fail_dead_letters(client: TestClient, worker: subprocess.Popen[bytes]) -> None:
    job = client.post(
        "/jobs",
        json={"workflow_name": "always_fail", "idempotency_key": "af-1", "input": {}},
    ).json()
    wait_until(lambda: fetch_job(client, job["id"])["status"] == "dead_lettered", timeout=15)
    step = fetch_job(client, job["id"])["steps"][0]
    assert step["status"] == "dead_lettered"
    assert step["attempt_count"] == 3


def test_replay_dead_letter(client: TestClient, worker: subprocess.Popen[bytes]) -> None:
    job = client.post(
        "/jobs",
        json={"workflow_name": "always_fail", "idempotency_key": "af-replay", "input": {}},
    ).json()
    wait_until(lambda: fetch_job(client, job["id"])["status"] == "dead_lettered", timeout=15)
    stop_worker(worker)
    # Replay resets attempt_count; still always_fail, so it will dead-letter again
    # unless we... always_fail always fails. This test only checks replay is accepted
    # and a new attempt starts. Restart worker after replay.
    res = client.post(f"/jobs/{job['id']}/steps/run/replay")
    assert res.status_code == 200
    assert res.json()["status"] == "running"
    assert res.json()["steps"][0]["status"] == "pending"
    proc = start_worker(os.environ["DATABASE_URL"])
    try:
        wait_until(lambda: fetch_job(client, job["id"])["status"] == "dead_lettered", timeout=15)
        step = fetch_job(client, job["id"])["steps"][0]
        assert step["attempt_count"] == 3
        assert len(step["attempts"]) >= 4
    finally:
        stop_worker(proc)


def test_idempotent_effect_write_count_one(
    client: TestClient, worker: subprocess.Popen[bytes], database_url: str
) -> None:
    job = client.post(
        "/jobs",
        json={"workflow_name": "idempotent_effect", "idempotency_key": "idemp-1", "input": {}},
    ).json()
    wait_until(lambda: fetch_job(client, job["id"])["status"] == "succeeded")
    with connect(database_url, row_factory=dict_row) as conn:
        row = conn.execute("SELECT * FROM lab_effects").fetchone()
    assert row is not None
    assert row["write_count"] == 1
