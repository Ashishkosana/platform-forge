# Durable workflow / job execution engine

A worker can die after performing an external side effect but before acknowledging the step. Retrying the whole job then charges twice, emails twice, or provisions twice. This engine keeps **per-step durable state**, claims work with a **lease and fencing token**, and makes that failure mode visible instead of hiding it.

**Guarantee: at-least-once step execution.** It does not claim exactly-once. Effectively-once side effects happen only when a handler cooperates with a stable idempotency key. The laboratory handlers prove both sides of that sentence.

## What it does

- Submit a named linear workflow (`charge → notify → provision` style).
- Persist `jobs`, `steps`, and `attempts` in PostgreSQL 16.
- Run workers as **separate OS processes** that `SELECT … FOR UPDATE SKIP LOCKED`.
- Heartbeat a lease using **database `now()`**, not the worker clock.
- Steal expired leases; reject stale commits with a monotonic **fencing token**.
- Retry with **full jitter**, then dead-letter.
- Cancel, replay a dead-lettered step, inspect stuck work, scrape Prometheus metrics.

## Architecture (V1)

```text
client → FastAPI (submit/inspect) → PostgreSQL
                                      ↑
                         worker processes (claim / heartbeat / complete)
```

Workers do not share memory. Distribution is “N processes, one database.” There is no Kafka, Redis, Celery, or Temporal in V1. See [ARCHITECTURE.md](ARCHITECTURE.md) and [DECISIONS.md](DECISIONS.md).

### Invariants

1. Duplicate submit of `(workflow_name, idempotency_key)` returns the same job.
2. Step *k+1* stays `blocked` until step *k* is `succeeded`.
3. Only a matching `fencing_token` may commit a running step.
4. Lost jobs after `kill -9` is a bug. Duplicate **attempts** after `kill -9` are expected.
5. Naive `lab_effects.write_count` may be `> 1`. Idempotent handlers keep it at `1`.

## Failure demonstrations

Automated in `drills/` and walked through in [FAILURE_DRILLS.md](FAILURE_DRILLS.md):

- crash after side effect
- zombie worker (`SIGSTOP`) + fencing reject
- poison / dead-letter
- retry jitter
- cancel during hang
- concurrent duplicate submit
- clock: leases use SQL `now()`

## Benchmark evidence

Measured on this agent VM. **Not** a capacity rating. Full tables: [BENCHMARKS.md](BENCHMARKS.md).

| Setup | Result |
| --- | --- |
| 50 `noop` jobs, 1 worker, 64 B payload | **270 jobs/s**, schedule p50 **6.8 ms**, p99 **10.5 ms** |
| 50 `noop` jobs, 2 workers, 64 B payload | **274 jobs/s**, schedule p50 **4.5 ms** (throughput barely moved: the bench submits sequentially) |
| 50 `noop` jobs, 1 worker, 100 KiB payload | **153 jobs/s**, schedule p50 **28 ms** |

## Run locally

Requires Python 3.12 and PostgreSQL 16.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env

# Docker, if you have it:
docker compose up -d postgres

# Or local Postgres (this environment used this path):
#   createuser/createdb workflow / workflow

export DATABASE_URL=postgresql://workflow:workflow@127.0.0.1:5432/workflow
export DEMO_MODE=true
python -m workflow_engine.api          # http://127.0.0.1:43180
python -m workflow_engine.worker       # another terminal; start two for steal demos
```

Ops console (engineering, not a pretty dashboard): open `/` when `DEMO_MODE=true`. Start a worker, submit `crash_naive`, SIGKILL, start another worker, watch `lab_effects.write_count`.

CLI:

```bash
workflow submit noop --key k1
workflow get <job-id>
workflow list --stuck
workflow cancel <job-id>
workflow replay <job-id> run
```

Tests (real Postgres, real worker subprocesses):

```bash
export TEST_DATABASE_URL=postgresql://workflow:workflow@127.0.0.1:5432/workflow_test
pytest -q
```

## Limitations

- Linear pipelines only. No DAG, signals, or long timers.
- Handler timeout is cooperative (Python cannot kill a stuck thread).
- Polling, not `LISTEN/NOTIFY`.
- No authentication. Demo worker kill endpoints exist only with `DEMO_MODE=true` and only signal processes this API started. `.env.example` defaults `DEMO_MODE=false`.
- `http_post` is allowlisted to `ALLOWED_HTTP_HOSTS` to reduce SSRF.
- Public deploy of workers + Postgres is **not** a Vercel-shaped app.

## What V2 would require

A measured problem. Candidates (not a roadmap): skip-locked saturation, need for hours-long sleep/signals, or payload bloat on the claim path. Write [V2_PROPOSAL.md](V2_PROPOSAL.md) only with numbers. There is no V2 code yet because V1 still holds for the benches we ran.
