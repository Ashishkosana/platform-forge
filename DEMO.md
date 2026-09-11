# 60-second demo — Platform Forge

Goal: show **claim → crash after effect → lease steal → fenced recovery**, using code that already exists. Do not skip the naive vs idempotent pair; that pair is the argument.

Requires the API and at least one worker from the README “Run locally” section (`DEMO_MODE=true` if you want console buttons).

## Terminal path (preferred)

```bash
# Terminal A — API
export DATABASE_URL=postgresql://workflow:workflow@127.0.0.1:5432/workflow
export DEMO_MODE=true
python -m workflow_engine.api

# Terminal B — worker
export DATABASE_URL=postgresql://workflow:workflow@127.0.0.1:5432/workflow
python -m workflow_engine.worker
```

**Happy path (~10 s)**

```bash
workflow submit noop --key demo-ok
workflow get <job-id>          # status succeeded, one attempt
```

**The interesting path (~45 s)**

```bash
workflow submit crash_naive --key demo-naive
# Worker process exits on attempt 1 after incrementing lab_effects.
# Start a second worker (or wait for the process supervisor you use).
python -m workflow_engine.worker
workflow get <job-id>
# Expect: job succeeded, attempt_count >= 2, lab_effects.write_count >= 2
```

Then the contrast:

```bash
workflow submit crash_idempotent --key demo-once
# Recover the same way.
# Expect: attempt_count >= 2, lab_effects.write_count == 1
```

That is at-least-once **attempts** vs an idempotent **effect**. Do not describe the first job as exactly-once.

**Zombie / fence (~60 s, optional)**

```bash
workflow submit hang --key demo-hang --input '{"seconds": 30}'
# SIGSTOP the worker PID. Wait past LEASE_TTL (tests use 2s; .env.example default is 15s).
# Start another worker. SIGCONT the first.
# Only one succeeded attempt; the stale complete is rejected_fence.
```

Automated: `pytest drills/test_failure_drills.py -q`

## Console path

With `DEMO_MODE=true`, open http://127.0.0.1:43180/

1. Start worker.
2. Submit `linear_demo` — steps pending → running → succeeded.
3. Submit `crash_naive` — SIGKILL (console only signals API-spawned PIDs) — start another worker — inspect `write_count`.
4. Submit `hang` — SIGSTOP — wait for lease — second worker — SIGCONT — one success.

## Recording a GIF (optional)

Do not paste invented JSON into the README. If you want a visual:

1. Record the two `crash_*` submits and `workflow get` output with [asciinema](https://asciinema.org/) or a 1280×720 terminal capture.
2. Keep it under 60 seconds. Cut venv/pip.
3. Host the file yourself; this repo does not ship a generated GIF.

What the clip must show: two attempts on naive, `write_count >= 2`; two attempts on idempotent, `write_count == 1`.
