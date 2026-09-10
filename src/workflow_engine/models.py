from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

JOB_STATUSES = frozenset({"queued", "running", "succeeded", "dead_lettered", "cancelled"})
STEP_STATUSES = frozenset(
    {"pending", "blocked", "running", "succeeded", "dead_lettered", "cancelled"}
)
TERMINAL_JOB = frozenset({"succeeded", "dead_lettered", "cancelled"})
TERMINAL_STEP = frozenset({"succeeded", "dead_lettered", "cancelled"})


@dataclass(frozen=True)
class StepContext:
    job_id: UUID
    step_id: UUID
    step_name: str
    seq: int
    attempt_count: int
    fencing_token: int
    worker_id: str
    idempotency_key: str
    workflow_name: str
    job_input: dict[str, Any]
    upstream_outputs: dict[str, Any]
    should_stop: Any
    get_conn: Any


@dataclass
class JobView:
    id: UUID
    workflow_name: str
    idempotency_key: str
    status: str
    input: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    steps: list[dict[str, Any]] = field(default_factory=list)
