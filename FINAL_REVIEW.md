# Final review — Project 1

## What works

- Linear durable workflows on PostgreSQL 16 with Python 3.12 workers.
- Lease + heartbeat + fencing token (`claims.py`).
- Idempotent submit, cancel, dead-letter, replay.
- Ops console at `/` and CLI `workflow`.
- pytest: **25 passed, 1 skipped** (Postgres bounce gated). Includes real worker subprocesses.

## What was measured

See BENCHMARKS.md. Headline: 50 noop jobs, 1 worker, 64 B → **270 jobs/s**, schedule p50 **6.8 ms**. 100 KiB → **153 jobs/s**. Two workers did not double throughput under sequential submit.

## What failed during development

- Crash handlers that always `os._exit(1)` livelocked recovery. Changed to crash on attempt 1 only (documented lab concession).
- Worker `stdout=PIPE` without a reader blocked the herd drill. Switched tests to `DEVNULL`.
- pytest fixtures in `tests/conftest.py` were invisible to `drills/`. Moved to repo-root `conftest.py`.

## Bugs discovered

- Claim SQL originally joined `steps` twice for previous_status; replaced with CTE fields to avoid UPDATE/FROM ambiguity.
- Demo/API worker spawn used piped stdout (same deadlock risk). Fixed.

## Architecture changes because of evidence

- None that add components. Crash-handler attempt gate is a lab handler change, not a queue change.
- No Kafka/Redis/LISTEN added.

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

Auth, non-superuser DB role, scrape all worker `/metrics` ports, payload off-row if 100 KiB becomes normal, LISTEN only with a latency SLO.

## GitHub / three-repo status

This Cursor workspace is **not authenticated to GitHub** (`gh auth status` logged out). Separate GitHub repositories `durable-workflow-engine`, `ai-reliability-control-plane`, and `realtime-notification-platform` were **not created** to avoid colliding with an unknown account. **Owner action:** connect GitHub (Create repo pill, or `gh auth login`) and split remotes. Do not assume this Origin-backed workspace is those three names.

## CI

`.github/workflows/ci.yml` is written. It has not run on GitHub because there is no GitHub remote. Local stand-in: `ruff`, `mypy`, `pytest`.
