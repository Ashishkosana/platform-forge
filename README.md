# Platform Forge

A durable **step runner**: linear workflows in PostgreSQL, claimed by OS-process workers with leases and fencing tokens.

**Stack:** Python 3.12 · FastAPI · PostgreSQL 16 · psycopg 3 · pytest (real workers, not mocked SQL)

**Problems this repo actually solves:** a worker can die after a side effect but before ack; two workers can overlap after a lease expires; duplicate submits must not create duplicate jobs; retries must not livelock the fleet.

**Why it is interesting:** the interesting bugs are in *recovery*, not in “calling a handler.” The code makes at-least-once visible (naive `write_count >= 2`) and shows when a handler protocol makes the *effect* once (`write_count == 1`). It does not claim exactly-once execution.

**Guarantee: at-least-once step execution.** Effectively-once side effects happen only if the handler uses a stable key such as `{workflow}:{job_key}:{step}`.

This repository is **Project 1 only**. The AI control plane and the event platform are [separate remotes](SIBLING_PROJECTS.md). Python package name: `durable-workflow-engine` (`import workflow_engine`).

## How it is put together

```mermaid
flowchart LR
  Client -->|"POST /jobs, inspect, cancel"| API[FastAPI]
  API -->|"INSERT jobs + steps"| PG[(PostgreSQL)]
  subgraph workers [OS worker processes]
    W1[worker]
    W2[worker]
  end
  W1 -->|"SKIP LOCKED claim / heartbeat / complete"| PG
  W2 -->|"SKIP LOCKED claim / heartbeat / complete"| PG
```

The API never runs handlers. Workers never take client job HTTP. Distribution is N processes, one database. No Kafka, Redis, Celery, or Temporal.

```mermaid
sequenceDiagram
  participant C as Client
  participant A as API
  participant P as Postgres
  participant W as Worker
  C->>A: POST /jobs
  A->>P: job + step 1 pending, rest blocked
  P-->>W: NOTIFY workflow_wake (V2)
  W->>P: CLAIM SKIP LOCKED, fencing_token++
  loop lease
    W->>P: heartbeat WHERE token AND worker_id
  end
  W->>W: run handler
  W->>P: complete WHERE fencing_token matches
  P->>P: unblock next step
```

Recovery when the runner dies after the effect:

```mermaid
sequenceDiagram
  participant A as Worker A
  participant P as Postgres
  participant B as Worker B
  A->>P: claim fence N, run effect
  Note over A: process dies before complete
  B->>P: lease expired, claim fence N+1
  A--xP: complete fence N (0 rows)
  B->>P: complete fence N+1
```

The fence stops a **stale row write**. It cannot un-send HTTP that already left the process.

## What it does

- Submit a named linear workflow (`charge → notify → provision` style).
- Persist `jobs`, `steps`, and `attempts` in PostgreSQL 16.
- Claim with `SELECT … FOR UPDATE SKIP LOCKED`.
- Heartbeat the lease using **database `now()`**, not the worker clock.
- Steal expired leases; reject stale commits with a monotonic **fencing token**.
- Retry with **full jitter**, then dead-letter.
- Cancel, replay a dead-lettered step, inspect stuck work, scrape Prometheus metrics.
- V2: `WAKE_MODE=listen` (default) — `NOTIFY workflow_wake` on submit, successful complete, and replay. Idle workers `LISTEN` with the poll interval as timeout fallback. `WAKE_MODE=poll` is the V1 path.

### Invariants

1. Duplicate submit of `(workflow_name, idempotency_key)` returns the same job.
2. Step *k+1* stays `blocked` until step *k* is `succeeded`.
3. Only a matching `fencing_token` may commit a running step.
4. Lost **jobs** after `kill -9` is a bug. Duplicate **attempts** after `kill -9` are expected.
5. Naive `lab_effects.write_count` may be `> 1`. Idempotent handlers keep it at `1`.

Details: [ARCHITECTURE.md](ARCHITECTURE.md), [DECISIONS.md](DECISIONS.md).

## 60-second demo

Submit `crash_naive` → worker dies after the write → another worker steals the lease → the naive counter is `>= 2`. That is the product. Commands: [DEMO.md](DEMO.md).

## Failure demonstrations

Automated in `drills/` and walked through in [FAILURE_DRILLS.md](FAILURE_DRILLS.md):

- crash after side effect (naive vs idempotent)
- zombie worker (`SIGSTOP`) + fencing reject
- poison / dead-letter
- retry jitter
- cancel during hang
- concurrent duplicate submit
- clock: leases use SQL `now()`

## Benchmark evidence

Measured on a 4-CPU / 15 GiB agent VM. **Not** a capacity rating. Full tables and method: [BENCHMARKS.md](BENCHMARKS.md). The harness used `POLL_INTERVAL_SECONDS=0.05` and sequential submit, so schedule latency is backlog drain, not idle wait.

| Setup | Result |
| --- | --- |
| 50 `noop` jobs, 1 worker, 64 B payload | **270 jobs/s**, schedule p50 **6.8 ms**, p99 **10.5 ms** |
| 50 `noop` jobs, 2 workers, 64 B payload | **274 jobs/s**, schedule p50 **4.5 ms** (submit is the bottleneck) |
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

export DATABASE_URL=postgresql://workflow:workflow@127.0.0.1:5432/workflow
export DEMO_MODE=true
python -m workflow_engine.api          # http://127.0.0.1:43180
python -m workflow_engine.worker       # another terminal; start two for steal demos
```

Ops console (engineering, not a product dashboard): open `/` when `DEMO_MODE=true`. `.env.example` defaults `DEMO_MODE=false`.

CLI:

```bash
workflow submit noop --key k1
workflow get <job-id>
workflow list --stuck
workflow cancel <job-id>
workflow replay <job-id> run
```

## Tests and CI

Real Postgres, real worker subprocesses. GitHub Actions: `.github/workflows/ci.yml` (ruff, ruff format, mypy, pytest) with Postgres 16.

```bash
export TEST_DATABASE_URL=postgresql://workflow:workflow@127.0.0.1:5432/workflow_test
pytest -q
ruff check src tests drills scripts
ruff format --check src tests drills scripts
mypy src
```

Postgres bounce is skipped unless `DRILL_PG_BOUNCE=1` (unsafe on a shared instance).

## Docs

| File | What it is |
| --- | --- |
| [DEMO.md](DEMO.md) | Shortest interesting run |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Processes, SQL, state machines |
| [DECISIONS.md](DECISIONS.md) | Why Postgres, why fencing, why LISTEN as wake |
| [FAILURE_DRILLS.md](FAILURE_DRILLS.md) | What we actually ran |
| [BENCHMARKS.md](BENCHMARKS.md) | Measured numbers and method limits |
| [INTERVIEW_GUIDE.md](INTERVIEW_GUIDE.md) | How to explain and defend this |
| [SECURITY.md](SECURITY.md) | Auth (none), SSRF allowlist, demo PIDs |
| [V2_PROPOSAL.md](V2_PROPOSAL.md) | LISTEN/NOTIFY wake (implemented) |

## Limitations

- Linear pipelines only. No DAG, signals, or long timers.
- Handler timeout is cooperative (Python cannot kill a stuck thread).
- No authentication. Demo worker-kill endpoints exist only with `DEMO_MODE=true` and only signal processes this API started.
- `http_post` is allowlisted to `ALLOWED_HTTP_HOSTS`.
- Prometheus counters are per process; scraping only the API undercounts workers. The durable truth is the `attempts` table.
- Public deploy of workers + Postgres is not a serverless app.
