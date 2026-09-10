from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest

REGISTRY = CollectorRegistry()

JOBS_SUBMITTED = Counter(
    "workflow_jobs_submitted_total",
    "Jobs submitted (duplicate=true means idempotent replay)",
    ["workflow", "duplicate"],
    registry=REGISTRY,
)
STEPS_CLAIMED = Counter(
    "workflow_steps_claimed_total",
    "Steps claimed by a worker",
    ["step", "steal"],
    registry=REGISTRY,
)
STEPS_COMPLETED = Counter(
    "workflow_steps_completed_total",
    "Step completion attempts by outcome",
    ["step", "outcome"],
    registry=REGISTRY,
)
LEASE_EXPIRED = Counter(
    "workflow_lease_expired_total",
    "Claims that stole an expired lease",
    ["step"],
    registry=REGISTRY,
)
FENCING_REJECT = Counter(
    "workflow_fencing_reject_total",
    "Complete/heartbeat writes rejected by fencing token",
    ["step"],
    registry=REGISTRY,
)
HANDLER_SECONDS = Histogram(
    "workflow_handler_seconds",
    "Handler wall time",
    ["step"],
    registry=REGISTRY,
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60),
)
RUNNABLE_STEPS = Gauge(
    "workflow_runnable_steps",
    "Steps currently claimable",
    registry=REGISTRY,
)
OLDEST_RUNNABLE_AGE = Gauge(
    "workflow_oldest_runnable_age_seconds",
    "Age of oldest claimable step",
    registry=REGISTRY,
)
DEAD_LETTERED_STEPS = Gauge(
    "workflow_dead_lettered_steps",
    "Dead-lettered steps",
    registry=REGISTRY,
)
JOBS_BY_STATUS = Gauge(
    "workflow_jobs_by_status",
    "Jobs by status",
    ["status"],
    registry=REGISTRY,
)


def render_metrics() -> bytes:
    return generate_latest(REGISTRY)
