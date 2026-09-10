from __future__ import annotations

import time
from uuid import uuid4

from workflow_engine.claims import ClaimedStep
from workflow_engine.config import Settings
from workflow_engine.worker import Heartbeat


def test_heartbeat_tolerates_two_transient_errors(client: object, monkeypatch: object) -> None:
    calls = {"n": 0}

    def flaky(*_args: object, **_kwargs: object) -> str:
        calls["n"] += 1
        if calls["n"] <= 2:
            raise RuntimeError("blip")
        return "running"

    monkeypatch.setattr("workflow_engine.worker.heartbeat", flaky)  # type: ignore[attr-defined]
    claimed = ClaimedStep(
        step_id=uuid4(),
        job_id=uuid4(),
        seq=1,
        name="noop",
        fencing_token=1,
        attempt_count=1,
        max_attempts=3,
        timeout_seconds=5,
        steal=False,
        workflow_name="noop",
        idempotency_key="k",
        job_input={},
        upstream_outputs={},
        job_status="running",
    )
    settings = Settings(heartbeat_interval_seconds=0.05, lease_ttl_seconds=15)
    hb = Heartbeat(claimed, "w", settings)
    hb.start()
    time.sleep(0.35)
    lost = hb.lost.is_set()
    hb.join()
    assert calls["n"] >= 3
    assert lost is False
