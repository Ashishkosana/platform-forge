# Benchmarks

**Rule:** every number below was produced by `scripts/bench.py` or pytest on this machine. Nothing is estimated.

## Environment

| Field | Value |
| --- | --- |
| Measured at | 2026-09-10T21:11:19Z |
| Host | Cursor Cloud Agent VM |
| OS | Linux 6.12.94+ x86_64 glibc 2.39 |
| CPU | 4 × Intel Xeon, 1 thread/core |
| Memory | 15 GiB |
| Python | 3.12.3 |
| PostgreSQL | 16.15, `max_connections=100`, local Unix/TCP on 127.0.0.1 |
| Method | `scripts/bench.py`: start API + N worker processes, sequential HTTP submit of M jobs, wait until all `succeeded`, compute wall jobs/s and schedule latency (`jobs.created_at` → min `attempts.started_at`). **The harness sets `POLL_INTERVAL_SECONDS=0.05`**, not the production default `0.25`. Jobs are submitted back-to-back, so schedule latency is mostly backlog drain, not idle-worker wait. Do not use these p50 numbers as evidence that LISTEN/NOTIFY is unnecessary. |

Limitations of the method:

- Sequential submit means wall jobs/s includes API insert time. Two workers will not double this number if submit is the bottleneck.
- API, workers, Postgres, and the bench share one VM.
- 50 jobs is a correctness/latency probe, not a saturation test.
- This is **not** a production capacity rating.

## Throughput and schedule latency

| Jobs | Workers | Payload | Wall s | Jobs/s | p50 ms | p95 ms | p99 ms | max ms |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 50 noop | 1 | 64 B | 0.185 | **270** | 6.78 | 10.28 | 10.50 | 10.50 |
| 50 noop | 2 | 64 B | 0.183 | **274** | 4.55 | 7.19 | 7.25 | 7.25 |
| 50 noop | 1 | 100 KiB | 0.327 | **153** | 28.27 | 52.60 | 52.88 | 52.88 |

Observation: adding a second worker barely moved throughput and **did** cut schedule latency. 100 KiB input roughly halved jobs/s and ~4× p50 schedule latency. That is payload on the job row, not a Kafka-shaped problem yet.

## Lease recovery

**NOT MEASURED** as a dedicated timer script. The crash drills use `LEASE_TTL_SECONDS=2`. Recovery cannot be faster than lease expiry plus one poll interval. A production TTL of 15s implies steal in ~15–20s if heartbeats stop.

## Lock contention (many workers, one job)

**NOT MEASURED** as a published histogram. Qualitatively, `SKIP LOCKED` means extra workers spin on poll rather than block. Pytest zombie drill uses two workers on one `hang` job without deadlock.

## Retry amplification

Herd drill: `DRILL_HERD_SIZE` default 40 `always_fail` jobs, `max_attempts=3`, test backoff cap 0.4s. All reached `dead_lettered`. Attempt count on `herd-0` was 3. Full production-like 1000-job herd was **not** run on this VM in this pass.

## Durability (lost jobs)

Crash drills: job row still present after `os._exit(1)`. Lost-job count in those tests: **0**.

## What would justify V2

| Signal | Seen? |
| --- | --- |
| Skip-locked saturates, CPU idle | No (tiny n) |
| Schedule p99 ≫ poll interval | No (p99 ~10 ms vs 50–250 ms poll; submit path dominates) |
| 100 KiB payloads wreck claim path | Latency rose; still acceptable for V1. Revisit if payloads are routinely large. |

No V2 implementation. See DECISIONS.md.
