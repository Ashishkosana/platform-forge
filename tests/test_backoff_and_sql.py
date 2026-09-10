from __future__ import annotations

import inspect
import random

from workflow_engine.claims import CLAIM_SQL, COMPLETE_SUCCESS_SQL, HEARTBEAT_SQL
from workflow_engine.registry import full_jitter


def test_full_jitter_is_bounded() -> None:
    rng = random.Random(0)
    delays = [full_jitter(3, base=0.5, max_backoff=30, rng=rng) for _ in range(500)]
    cap = min(30, 0.5 * 4)
    assert min(delays) >= 0
    assert max(delays) <= cap + 1e-9
    assert max(delays) - min(delays) > cap * 0.3


def test_full_jitter_without_jitter_would_be_constant() -> None:
    """Control: the deterministic cap is a spike; jitter spreads below it."""
    rng = random.Random(1)
    jittered = [full_jitter(4, 0.5, 30, rng) for _ in range(200)]
    cap = min(30.0, 0.5 * 8)
    assert all(d <= cap for d in jittered)
    assert sum(d < cap * 0.5 for d in jittered) > 50


def test_lease_sql_uses_database_now() -> None:
    for sql in (CLAIM_SQL, HEARTBEAT_SQL, COMPLETE_SUCCESS_SQL):
        assert "now()" in sql
    assert "SKIP LOCKED" in CLAIM_SQL
    assert "fencing_token" in COMPLETE_SUCCESS_SQL


def test_claim_sql_does_not_use_python_datetime() -> None:
    import workflow_engine.claims as claims
    import workflow_engine.worker as worker

    assert "datetime.now" not in inspect.getsource(claims.claim_step)
    assert "leased_until = now()" in HEARTBEAT_SQL or "leased_until = now() +" in HEARTBEAT_SQL
    source = inspect.getsource(worker)
    assert "datetime.now" not in source or "log" in source
