"""Claim, heartbeat, and complete protocols.

SQL in this module is the contract. Leases use PostgreSQL now().
A stale fencing token cannot commit step state.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from psycopg import Connection
from psycopg.types.json import Json

from workflow_engine.metrics import (
    FENCING_REJECT,
    JOBS_SUBMITTED,
    LEASE_EXPIRED,
    STEPS_CLAIMED,
    STEPS_COMPLETED,
)
from workflow_engine.registry import REGISTRY, full_jitter

# Claimable: pending whose run_after has arrived, OR running whose lease expired.
CLAIM_SQL = """
WITH picked AS (
  SELECT s.id, s.status AS previous_status, s.fencing_token AS previous_token
  FROM steps s
  JOIN jobs j ON j.id = s.job_id
  WHERE j.status IN ('queued', 'running')
    AND (
      (s.status = 'pending' AND s.run_after <= now())
      OR
      (s.status = 'running' AND s.leased_until < now())
    )
  ORDER BY s.run_after, s.id
  FOR UPDATE OF s SKIP LOCKED
  LIMIT 1
)
UPDATE steps s
SET status          = 'running',
    worker_id       = %(worker_id)s,
    fencing_token   = s.fencing_token + 1,
    leased_until    = now() + %(lease_ttl)s::interval,
    attempt_count   = s.attempt_count + 1,
    updated_at      = now()
FROM picked
WHERE s.id = picked.id
RETURNING
  s.id,
  s.job_id,
  s.seq,
  s.name,
  s.fencing_token,
  s.attempt_count,
  s.max_attempts,
  s.timeout_seconds,
  picked.previous_status,
  picked.previous_token
"""

HEARTBEAT_SQL = """
UPDATE steps AS s
SET leased_until = now() + %(lease_ttl)s::interval,
    updated_at   = now()
FROM jobs AS j
WHERE s.id = %(step_id)s
  AND s.job_id = j.id
  AND s.worker_id = %(worker_id)s
  AND s.fencing_token = %(fencing_token)s
  AND s.status = 'running'
RETURNING s.id, j.status AS job_status
"""

COMPLETE_SUCCESS_SQL = """
UPDATE steps AS s
SET status        = 'succeeded',
    output        = %(output)s,
    last_error    = NULL,
    leased_until  = NULL,
    worker_id     = NULL,
    updated_at    = now()
FROM jobs AS j
WHERE s.id = %(step_id)s
  AND s.job_id = j.id
  AND s.fencing_token = %(fencing_token)s
  AND s.status = 'running'
  AND j.status IN ('queued', 'running')
RETURNING s.id, s.job_id, s.seq
"""


@dataclass
class ClaimedStep:
    step_id: UUID
    job_id: UUID
    seq: int
    name: str
    fencing_token: int
    attempt_count: int
    max_attempts: int
    timeout_seconds: int
    steal: bool
    workflow_name: str
    idempotency_key: str
    job_input: dict[str, Any]
    upstream_outputs: dict[str, Any]
    job_status: str


def submit_job(
    conn: Connection,  # type: ignore[type-arg]
    workflow_name: str,
    idempotency_key: str,
    job_input: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    wf = REGISTRY.get(workflow_name)
    if wf is None:
        raise ValueError("unknown_workflow")

    job_id = uuid4()
    inserted = conn.execute(
        """
        INSERT INTO jobs (id, workflow_name, idempotency_key, status, input)
        VALUES (%s, %s, %s, 'queued', %s)
        ON CONFLICT (workflow_name, idempotency_key) DO NOTHING
        RETURNING id
        """,
        (job_id, workflow_name, idempotency_key, Json(job_input)),
    ).fetchone()

    if inserted is None:
        existing = conn.execute(
            """
            SELECT id FROM jobs
            WHERE workflow_name = %s AND idempotency_key = %s
            """,
            (workflow_name, idempotency_key),
        ).fetchone()
        if existing is None:
            raise RuntimeError("idempotent submit lost the job row")
        conn.commit()
        JOBS_SUBMITTED.labels(workflow=workflow_name, duplicate="true").inc()
        return load_job(conn, existing["id"]), False

    real_id = inserted["id"]
    for seq, step in enumerate(wf.steps, start=1):
        conn.execute(
            """
            INSERT INTO steps (
              id, job_id, seq, name, status, run_after,
              max_attempts, timeout_seconds
            )
            VALUES (%s, %s, %s, %s, %s, now(), %s, %s)
            """,
            (
                uuid4(),
                real_id,
                seq,
                step.name,
                "pending" if seq == 1 else "blocked",
                step.max_attempts,
                step.timeout_seconds,
            ),
        )
    conn.execute("NOTIFY workflow_wake")
    conn.commit()
    JOBS_SUBMITTED.labels(workflow=workflow_name, duplicate="false").inc()
    return load_job(conn, real_id), True


def load_job(conn: Connection, job_id: UUID) -> dict[str, Any]:  # type: ignore[type-arg]
    job = conn.execute("SELECT * FROM jobs WHERE id = %s", (job_id,)).fetchone()
    if job is None:
        raise KeyError(str(job_id))
    steps = conn.execute(
        "SELECT * FROM steps WHERE job_id = %s ORDER BY seq",
        (job_id,),
    ).fetchall()
    attempts = conn.execute(
        """
        SELECT a.*
        FROM attempts a
        JOIN steps s ON s.id = a.step_id
        WHERE s.job_id = %s
        ORDER BY a.started_at, a.id
        """,
        (job_id,),
    ).fetchall()
    by_step: dict[UUID, list[dict[str, Any]]] = {}
    for attempt in attempts:
        by_step.setdefault(attempt["step_id"], []).append(dict(attempt))
    step_views = []
    for step in steps:
        view = dict(step)
        view["attempts"] = by_step.get(step["id"], [])
        step_views.append(view)
    result = dict(job)
    result["steps"] = step_views
    return result


def list_jobs(
    conn: Connection,  # type: ignore[type-arg]
    status: str | None = None,
    stuck: bool = False,
    limit: int = 50,
) -> list[dict[str, Any]]:
    limit = max(1, min(limit, 200))
    if stuck:
        rows = conn.execute(
            """
            SELECT DISTINCT j.id, j.created_at
            FROM jobs j
            JOIN steps s ON s.job_id = j.id
            WHERE j.status IN ('queued', 'running')
              AND (
                (s.status = 'running' AND s.leased_until < now())
                OR (s.status = 'pending' AND s.run_after <= now() - interval '30 seconds')
              )
            ORDER BY j.created_at DESC
            LIMIT %s
            """,
            (limit,),
        ).fetchall()
        return [load_job(conn, row["id"]) for row in rows]

    if status:
        rows = conn.execute(
            """
            SELECT id FROM jobs
            WHERE status = %s
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (status, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id FROM jobs ORDER BY created_at DESC LIMIT %s",
            (limit,),
        ).fetchall()
    return [load_job(conn, row["id"]) for row in rows]


def cancel_job(conn: Connection, job_id: UUID) -> dict[str, Any]:  # type: ignore[type-arg]
    row = conn.execute(
        """
        UPDATE jobs
        SET status = 'cancelled', updated_at = now()
        WHERE id = %s AND status IN ('queued', 'running')
        RETURNING id
        """,
        (job_id,),
    ).fetchone()
    if row is None:
        exists = conn.execute("SELECT status FROM jobs WHERE id = %s", (job_id,)).fetchone()
        if exists is None:
            raise KeyError(str(job_id))
        raise PermissionError("conflict_terminal")
    conn.execute(
        """
        UPDATE steps
        SET status = 'cancelled',
            leased_until = NULL,
            worker_id = NULL,
            updated_at = now()
        WHERE job_id = %s AND status IN ('pending', 'blocked', 'running')
        """,
        (job_id,),
    )
    conn.commit()
    return load_job(conn, job_id)


def replay_step(conn: Connection, job_id: UUID, step_name: str) -> dict[str, Any]:  # type: ignore[type-arg]
    job = conn.execute("SELECT * FROM jobs WHERE id = %s", (job_id,)).fetchone()
    if job is None:
        raise KeyError(str(job_id))
    step = conn.execute(
        "SELECT * FROM steps WHERE job_id = %s AND name = %s",
        (job_id, step_name),
    ).fetchone()
    if step is None:
        raise KeyError(step_name)
    if step["status"] != "dead_lettered":
        raise PermissionError("not_dead_lettered")
    conn.execute(
        """
        UPDATE steps
        SET status = 'pending',
            run_after = now(),
            last_error = NULL,
            leased_until = NULL,
            worker_id = NULL,
            attempt_count = 0,
            updated_at = now()
        WHERE id = %s AND status = 'dead_lettered'
        """,
        (step["id"],),
    )
    conn.execute(
        """
        UPDATE jobs
        SET status = 'running', updated_at = now()
        WHERE id = %s AND status = 'dead_lettered'
        """,
        (job_id,),
    )
    conn.execute("NOTIFY workflow_wake")
    conn.commit()
    return load_job(conn, job_id)


def claim_step(
    conn: Connection,  # type: ignore[type-arg]
    worker_id: str,
    lease_ttl: str,
) -> ClaimedStep | None:
    row = conn.execute(
        CLAIM_SQL,
        {"worker_id": worker_id, "lease_ttl": lease_ttl},
    ).fetchone()
    if row is None:
        conn.rollback()
        return None

    steal = row["previous_status"] == "running"
    conn.execute(
        """
        INSERT INTO attempts (step_id, fencing_token, worker_id, outcome)
        VALUES (%s, %s, %s, 'started')
        ON CONFLICT (step_id, fencing_token) DO NOTHING
        """,
        (row["id"], row["fencing_token"], worker_id),
    )
    job = conn.execute("SELECT * FROM jobs WHERE id = %s", (row["job_id"],)).fetchone()
    if job is None:
        conn.rollback()
        return None
    if job["status"] == "cancelled":
        _finish_attempt(
            conn, row["id"], row["fencing_token"], "cancelled", "job cancelled during claim"
        )
        conn.execute(
            """
            UPDATE steps
            SET status = 'cancelled', leased_until = NULL, worker_id = NULL, updated_at = now()
            WHERE id = %s AND fencing_token = %s AND status = 'running'
            """,
            (row["id"], row["fencing_token"]),
        )
        conn.commit()
        return None
    if job["status"] == "queued":
        conn.execute(
            """
            UPDATE jobs SET status = 'running', updated_at = now()
            WHERE id = %s AND status = 'queued'
            """,
            (row["job_id"],),
        )
        job = conn.execute("SELECT * FROM jobs WHERE id = %s", (row["job_id"],)).fetchone()

    upstream = conn.execute(
        """
        SELECT name, output FROM steps
        WHERE job_id = %s AND seq < %s AND status = 'succeeded'
        ORDER BY seq
        """,
        (row["job_id"], row["seq"]),
    ).fetchall()
    conn.commit()

    STEPS_CLAIMED.labels(step=row["name"], steal=str(steal).lower()).inc()
    if steal:
        LEASE_EXPIRED.labels(step=row["name"]).inc()

    assert job is not None
    return ClaimedStep(
        step_id=row["id"],
        job_id=row["job_id"],
        seq=row["seq"],
        name=row["name"],
        fencing_token=row["fencing_token"],
        attempt_count=row["attempt_count"],
        max_attempts=row["max_attempts"],
        timeout_seconds=row["timeout_seconds"],
        steal=steal,
        workflow_name=job["workflow_name"],
        idempotency_key=job["idempotency_key"],
        job_input=dict(job["input"] or {}),
        upstream_outputs={u["name"]: u["output"] for u in upstream},
        job_status=job["status"],
    )


def heartbeat(
    conn: Connection,  # type: ignore[type-arg]
    step_id: UUID,
    worker_id: str,
    fencing_token: int,
    lease_ttl: str,
) -> str | None:
    """Return job status, 'cancelled' if the job/step was cancelled, or None if stolen."""
    row = conn.execute(
        HEARTBEAT_SQL,
        {
            "step_id": step_id,
            "worker_id": worker_id,
            "fencing_token": fencing_token,
            "lease_ttl": lease_ttl,
        },
    ).fetchone()
    conn.commit()
    if row is not None:
        return str(row["job_status"])
    meta = conn.execute(
        """
        SELECT j.status AS job_status, s.status AS step_status
        FROM steps s
        JOIN jobs j ON j.id = s.job_id
        WHERE s.id = %s AND s.fencing_token = %s
        """,
        (step_id, fencing_token),
    ).fetchone()
    if meta is not None and (
        meta["job_status"] == "cancelled" or meta["step_status"] == "cancelled"
    ):
        return "cancelled"
    return None


def _cancel_owned_step(conn: Connection, claimed: ClaimedStep, error: str) -> str:  # type: ignore[type-arg]
    conn.execute(
        """
        UPDATE steps
        SET status = 'cancelled', leased_until = NULL, worker_id = NULL, updated_at = now()
        WHERE id = %s AND fencing_token = %s AND status = 'running'
        """,
        (claimed.step_id, claimed.fencing_token),
    )
    _finish_attempt(conn, claimed.step_id, claimed.fencing_token, "cancelled", error)
    conn.commit()
    STEPS_COMPLETED.labels(step=claimed.name, outcome="cancelled").inc()
    return "cancelled"


def complete_success(
    conn: Connection,  # type: ignore[type-arg]
    claimed: ClaimedStep,
    output: dict[str, Any],
) -> str:
    """Return succeeded, cancelled, or rejected_fence."""
    row = conn.execute(
        COMPLETE_SUCCESS_SQL,
        {
            "output": Json(output),
            "step_id": claimed.step_id,
            "fencing_token": claimed.fencing_token,
        },
    ).fetchone()
    if row is None:
        job = conn.execute("SELECT status FROM jobs WHERE id = %s", (claimed.job_id,)).fetchone()
        if job and job["status"] == "cancelled":
            conn.rollback()
            return _cancel_owned_step(conn, claimed, "job cancelled before complete")
        conn.rollback()
        _record_fence_reject(conn, claimed, "stale complete_success")
        return "rejected_fence"
    _finish_attempt(conn, claimed.step_id, claimed.fencing_token, "succeeded", None)
    next_seq = claimed.seq + 1
    unblocked = conn.execute(
        """
        UPDATE steps
        SET status = 'pending', run_after = now(), updated_at = now()
        WHERE job_id = %s AND seq = %s AND status = 'blocked'
        RETURNING id
        """,
        (claimed.job_id, next_seq),
    ).fetchone()
    if unblocked is None:
        conn.execute(
            """
            UPDATE jobs
            SET status = 'succeeded', updated_at = now()
            WHERE id = %s AND status = 'running'
              AND NOT EXISTS (
                SELECT 1 FROM steps
                WHERE job_id = %s AND status <> 'succeeded'
              )
            """,
            (claimed.job_id, claimed.job_id),
        )
    conn.execute("NOTIFY workflow_wake")
    conn.commit()
    STEPS_COMPLETED.labels(step=claimed.name, outcome="succeeded").inc()
    return "succeeded"


def complete_failure(
    conn: Connection,  # type: ignore[type-arg]
    claimed: ClaimedStep,
    error: str,
    outcome: str,
    base_backoff: float,
    max_backoff: float,
    rng: random.Random,
    retryable: bool = True,
) -> str:
    job = conn.execute("SELECT status FROM jobs WHERE id = %s", (claimed.job_id,)).fetchone()
    job_status = job["status"] if job else "missing"

    still_ours = conn.execute(
        """
        SELECT id FROM steps
        WHERE id = %s AND fencing_token = %s AND status = 'running'
        """,
        (claimed.step_id, claimed.fencing_token),
    ).fetchone()
    if still_ours is None:
        if job_status == "cancelled":
            _finish_attempt(conn, claimed.step_id, claimed.fencing_token, "cancelled", error)
            conn.commit()
            STEPS_COMPLETED.labels(step=claimed.name, outcome="cancelled").inc()
            return "cancelled"
        _record_fence_reject(conn, claimed, f"stale complete ({outcome})")
        return "rejected_fence"

    if job_status == "cancelled":
        return _cancel_owned_step(conn, claimed, error)

    if retryable and claimed.attempt_count < claimed.max_attempts:
        delay = full_jitter(claimed.attempt_count, base_backoff, max_backoff, rng)
        conn.execute(
            """
            UPDATE steps
            SET status = 'pending',
                run_after = now() + %(delay)s::interval,
                leased_until = NULL,
                worker_id = NULL,
                last_error = %(error)s,
                updated_at = now()
            WHERE id = %(step_id)s
              AND fencing_token = %(token)s
              AND status = 'running'
            """,
            {
                "delay": f"{delay} seconds",
                "error": error[:2000],
                "step_id": claimed.step_id,
                "token": claimed.fencing_token,
            },
        )
        _finish_attempt(conn, claimed.step_id, claimed.fencing_token, outcome, error)
        conn.commit()
        STEPS_COMPLETED.labels(step=claimed.name, outcome=outcome).inc()
        return "retry"

    conn.execute(
        """
        UPDATE steps
        SET status = 'dead_lettered',
            last_error = %(error)s,
            leased_until = NULL,
            worker_id = NULL,
            updated_at = now()
        WHERE id = %(step_id)s
          AND fencing_token = %(token)s
          AND status = 'running'
        """,
        {
            "error": error[:2000],
            "step_id": claimed.step_id,
            "token": claimed.fencing_token,
        },
    )
    conn.execute(
        """
        UPDATE jobs
        SET status = 'dead_lettered', updated_at = now()
        WHERE id = %s AND status IN ('queued', 'running')
        """,
        (claimed.job_id,),
    )
    _finish_attempt(conn, claimed.step_id, claimed.fencing_token, outcome, error)
    conn.commit()
    STEPS_COMPLETED.labels(step=claimed.name, outcome="dead_lettered").inc()
    return "dead_lettered"


def _finish_attempt(
    conn: Connection,  # type: ignore[type-arg]
    step_id: UUID,
    fencing_token: int,
    outcome: str,
    error: str | None,
) -> None:
    conn.execute(
        """
        UPDATE attempts
        SET finished_at = now(), outcome = %s, error = %s
        WHERE step_id = %s AND fencing_token = %s AND outcome = 'started'
        """,
        (outcome, error[:2000] if error else None, step_id, fencing_token),
    )


def _record_fence_reject(conn: Connection, claimed: ClaimedStep, error: str) -> None:  # type: ignore[type-arg]
    FENCING_REJECT.labels(step=claimed.name).inc()
    STEPS_COMPLETED.labels(step=claimed.name, outcome="rejected_fence").inc()
    conn.execute(
        """
        UPDATE attempts
        SET finished_at = now(), outcome = 'rejected_fence', error = %s
        WHERE step_id = %s AND fencing_token = %s AND outcome = 'started'
        """,
        (error[:2000], claimed.step_id, claimed.fencing_token),
    )
    conn.commit()


def gauge_snapshot(conn: Connection) -> dict[str, Any]:  # type: ignore[type-arg]
    row = conn.execute(
        """
        SELECT
          count(*) FILTER (
            WHERE (s.status = 'pending' AND s.run_after <= now())
               OR (s.status = 'running' AND s.leased_until < now())
          ) AS runnable,
          extract(epoch FROM now() - min(s.run_after) FILTER (
            WHERE (s.status = 'pending' AND s.run_after <= now())
               OR (s.status = 'running' AND s.leased_until < now())
          )) AS oldest_age,
          count(*) FILTER (WHERE s.status = 'dead_lettered') AS dead_lettered
        FROM steps s
        JOIN jobs j ON j.id = s.job_id
        WHERE j.status IN ('queued', 'running', 'dead_lettered')
        """
    ).fetchone()
    jobs = conn.execute("SELECT status, count(*) AS n FROM jobs GROUP BY status").fetchall()
    return {
        "runnable": int(row["runnable"] or 0) if row else 0,
        "oldest_age": float(row["oldest_age"] or 0) if row else 0.0,
        "dead_lettered": int(row["dead_lettered"] or 0) if row else 0,
        "jobs_by_status": {j["status"]: int(j["n"]) for j in jobs},
    }


def refresh_gauges(conn: Connection) -> None:  # type: ignore[type-arg]
    from workflow_engine.metrics import (
        DEAD_LETTERED_STEPS,
        JOBS_BY_STATUS,
        OLDEST_RUNNABLE_AGE,
        RUNNABLE_STEPS,
    )

    snap = gauge_snapshot(conn)
    RUNNABLE_STEPS.set(snap["runnable"])
    OLDEST_RUNNABLE_AGE.set(snap["oldest_age"])
    DEAD_LETTERED_STEPS.set(snap["dead_lettered"])
    for status in ("queued", "running", "succeeded", "dead_lettered", "cancelled"):
        JOBS_BY_STATUS.labels(status=status).set(snap["jobs_by_status"].get(status, 0))


def jsonable(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [jsonable(v) for v in value]
    return value
