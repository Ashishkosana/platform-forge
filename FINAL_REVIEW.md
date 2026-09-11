# Final review — Project 1

## What works

- Linear durable workflows on PostgreSQL 16 with Python 3.12 workers.
- Lease + heartbeat + fencing token (`claims.py`).
- Idempotent submit, cancel, dead-letter, replay.
- Ops console at `/` and CLI `workflow`.
- pytest: **29 passed, 1 skipped** (Postgres bounce gated; count from the post-review run). Includes real worker subprocesses.

## What was measured

See BENCHMARKS.md. Headline: 50 noop jobs, 1 worker, 64 B → **270 jobs/s**, schedule p50 **6.8 ms**. 100 KiB → **153 jobs/s**. Two workers did not double throughput under sequential submit.

## What failed during development

- Crash handlers that always `os._exit(1)` livelocked recovery. Changed to crash on attempt 1 only (documented lab concession).
- Worker `stdout=PIPE` without a reader blocked the herd drill. Switched tests to `DEVNULL`.
- pytest fixtures in `tests/conftest.py` were invisible to `drills/`. Moved to repo-root `conftest.py`.

## Bugs discovered

- Claim SQL originally joined `steps` twice for previous_status; replaced with CTE fields to avoid UPDATE/FROM ambiguity.
- Demo/API worker spawn used piped stdout (same deadlock risk). Fixed.
- `complete_success` could mark a step succeeded after the job was cancelled. SQL now requires `jobs.status IN ('queued','running')`; worker prefers the cancel path over a handler return value.
- Stuck-job listing ignored job status; cancel left running steps stranded. Cancel now flips running steps; stuck filter is `queued`/`running` only.
- Demo SIGKILL/STOP/CONT accepted any PID. Signals are limited to API-spawned worker PIDs. `.env.example` defaults `DEMO_MODE=false`.
- A single heartbeat exception was treated as lease loss. Two consecutive failures are tolerated before `lost`.

## Architecture changes because of evidence

- None that add components. Crash-handler attempt gate is a lab handler change, not a queue change.
- No Kafka/Redis added. V2 added `LISTEN/NOTIFY` as worker *wake* only; claim path is still skip-locked + fencing.

## Known limitations

- No auth; demo process control is dangerous if exposed.
- Cooperative timeouts.
- Linear only.
- Worker Prometheus counters are per-process.
- Postgres bounce **NOT MEASURED** in this environment.
- 1000-job herd **NOT MEASURED** (40 used).
- Dedicated lease-recovery timer script **NOT MEASURED** (implied by 2s test TTL).

## Unverified claims

Any sentence of the form “handles production load” or “exactly-once” would be unverified. We do not make them.

## Operating instructions

README.md. Default API port **43180**. `DEMO_MODE=true` for the console worker buttons.

## Potential production improvements

Auth, non-superuser DB role, scrape all worker `/metrics` ports, payload off-row if 100 KiB becomes normal.

## GitHub

Private remote: https://github.com/Ashishkosana/platform-forge  
Siblings: `ai-reliability-control-plane`, `realtime-event-platform` (separate histories).

## CI

`.github/workflows/ci.yml`: ruff, ruff format, mypy, pytest against Postgres 16. Postgres bounce remains skipped unless `DRILL_PG_BOUNCE=1`.
