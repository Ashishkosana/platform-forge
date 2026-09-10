from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

from fastapi.testclient import TestClient


def test_health(client: TestClient) -> None:
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


def test_unknown_workflow(client: TestClient) -> None:
    res = client.post(
        "/jobs",
        json={"workflow_name": "nope", "idempotency_key": "k1", "input": {}},
    )
    assert res.status_code == 400
    assert res.json()["code"] == "unknown_workflow"


def test_submit_and_get(client: TestClient) -> None:
    res = client.post(
        "/jobs",
        json={"workflow_name": "noop", "idempotency_key": "k-noop", "input": {"a": 1}},
    )
    assert res.status_code == 201
    job = res.json()
    assert job["status"] == "queued"
    assert job["steps"][0]["status"] == "pending"
    got = client.get(f"/jobs/{job['id']}")
    assert got.status_code == 200
    assert got.json()["idempotency_key"] == "k-noop"


def test_duplicate_submit_returns_same_job(client: TestClient) -> None:
    body = {"workflow_name": "echo", "idempotency_key": "same", "input": {"x": 1}}
    first = client.post("/jobs", json=body)
    second = client.post("/jobs", json={**body, "input": {"x": 2}})
    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert first.json()["input"] == {"x": 1}


def test_concurrent_duplicate_submit(client: TestClient) -> None:
    body = {"workflow_name": "noop", "idempotency_key": "conc-1", "input": {}}

    def once() -> str:
        res = client.post("/jobs", json=body)
        assert res.status_code in {200, 201}
        return res.json()["id"]

    with ThreadPoolExecutor(max_workers=20) as pool:
        ids = [fut.result() for fut in as_completed([pool.submit(once) for _ in range(50)])]
    assert len(set(ids)) == 1
    listed = client.get("/jobs")
    matches = [j for j in listed.json()["jobs"] if j["idempotency_key"] == "conc-1"]
    assert len(matches) == 1
    assert len(matches[0]["steps"]) == 1


def test_cancel_queued(client: TestClient) -> None:
    job = client.post(
        "/jobs", json={"workflow_name": "hang", "idempotency_key": "c1", "input": {"seconds": 30}}
    ).json()
    res = client.post(f"/jobs/{job['id']}/cancel")
    assert res.status_code == 200
    assert res.json()["status"] == "cancelled"
    again = client.post(f"/jobs/{job['id']}/cancel")
    assert again.status_code == 409


def test_replay_rejected_if_not_dead_letter(client: TestClient) -> None:
    job = client.post(
        "/jobs", json={"workflow_name": "noop", "idempotency_key": "r1", "input": {}}
    ).json()
    res = client.post(f"/jobs/{job['id']}/steps/run/replay")
    assert res.status_code == 409


def test_metrics_and_console(client: TestClient) -> None:
    metrics = client.get("/metrics")
    assert metrics.status_code == 200
    assert "workflow_jobs_by_status" in metrics.text
    page = client.get("/")
    assert page.status_code == 200
    assert "at-least-once" in page.text


def test_stuck_excludes_cancelled(client: TestClient) -> None:
    job = client.post(
        "/jobs",
        json={"workflow_name": "hang", "idempotency_key": "stuck-c", "input": {"seconds": 30}},
    ).json()
    res = client.post(f"/jobs/{job['id']}/cancel")
    assert res.json()["status"] == "cancelled"
    stuck = client.get("/jobs?stuck=true").json()["jobs"]
    assert all(j["id"] != job["id"] for j in stuck)


def test_demo_kill_rejects_foreign_pid(client: TestClient) -> None:
    import os

    res = client.post(f"/demo/workers/{os.getpid()}/kill")
    assert res.status_code == 404
    assert res.json()["code"] == "not_owned"
