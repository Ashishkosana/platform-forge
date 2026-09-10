# Architecture — V1 durable step runner

This is the implemented system, not a target picture of Temporal.

## Processes

| Process | Role |
| --- | --- |
| `python -m workflow_engine.api` | FastAPI: submit, inspect, cancel, replay, metrics, ops console |
| `python -m workflow_engine.worker` | One or more OS processes; each has `WORKER_CONCURRENCY` slots (threads) |
| PostgreSQL 16 | System of record and the queue |

The API never runs handlers. Workers never accept client HTTP for jobs.

```text
POST /jobs  → INSERT jobs + steps (seq 1 pending, rest blocked)
worker      → CLAIM_SQL (SKIP LOCKED) → heartbeat thread → handler thread → complete
```

## State machines

Job: `queued → running → succeeded | dead_lettered | cancelled`

Step: `blocked | pending → running → succeeded | pending(run_after) | dead_lettered | cancelled`

`retry_wait` is not a status. It is `pending` with `run_after` in the future.

## Claim / lease / fence

SQL lives in `src/workflow_engine/claims.py` as `CLAIM_SQL`, `HEARTBEAT_SQL`, `COMPLETE_SUCCESS_SQL`.

- Claimable: `pending AND run_after <= now()` **or** `running AND leased_until < now()`.
- Claim increments `fencing_token` and `attempt_count`, sets `leased_until = now() + interval`.
- Heartbeat: `UPDATE … WHERE fencing_token = $token AND worker_id = $me AND status = 'running'`.
- Complete success: same predicate. Zero rows → `rejected_fence`. Do not unblock the next step.

Worker wall clocks are not used for `leased_until`.

## Why two workers can overlap

A lease is time-bounded. After expiry another worker may run the same step. The fence prevents the stale worker from writing **state**. It cannot recall an HTTP request that already left the process.

## Retry

`full_jitter` in `registry.py`: `U(0, min(max_backoff, base * 2^(attempt-1)))`. After `max_attempts`, the step and job become `dead_lettered`. Replay resets `attempt_count` to 0 for that step only.

## Laboratory handlers

Registered in `handlers.register_lab_workflows()`. Crash handlers (`crash_naive`, `crash_idempotent`) call `os._exit(1)` **on attempt 1 only** so a second worker can complete. That is a lab concession: a handler that always crashes after the effect would livelock. Production handlers must be idempotent; they must not be designed to abort the process.

## Observability

- JSON logs: `job_id`, `step_id`, `step_name`, `fencing_token`, `attempt_count`, `worker_id`, `event`
- API `/metrics`: Prometheus text. Gauges are SQL (`gauge_snapshot`). Counters are **per process** (API vs each worker). Scraping only the API undercounts worker counters. The durable truth is the `attempts` table.
- `GET /jobs/{id}` is the state-machine dump.

## Demo mode

`DEMO_MODE=true` enables `/demo/workers/*` (start, SIGKILL, SIGSTOP, SIGCONT) **only for worker PIDs this API process spawned**. The default in `.env.example` is `false`. Set `DEMO_MODE=true` locally when you want the console buttons. Never enable it on a shared network.
