# Decisions

## ADR 1 — PostgreSQL as the queue

**Context:** Need durable claim + step state in one place.

**Options:** Redis/SQS/Kafka + Postgres; Postgres `SKIP LOCKED` only.

**Decision:** Postgres only.

**Reason:** Claim, fence, and persist attempts in one transaction. A broker would dual-write.

**Trade-off:** Throughput and fairness are whatever skip-locked gives. No independent consumer groups.

**Reconsider when:** Claim latency or lock wait saturates while CPUs are idle (not observed at 50 noop jobs).

## ADR 2 — Polling as the correctness path

**Decision (V1):** `POLL_INTERVAL_SECONDS` (default 0.25s, tests 0.05s).

**Reason:** Polling is correct under load. LISTEN is a latency optimization with payload and connection caveats.

**Evidence:** The published bench uses a 50ms poll and a pre-queued batch, so p50 4.5–6.8 ms does **not** measure idle wait.

## ADR 10 — LISTEN/NOTIFY as wake, poll as fallback (V2)

**Decision:** Default `WAKE_MODE=listen`. `NOTIFY workflow_wake` on submit, successful complete (next step), and replay. Workers `LISTEN` with the poll interval as **timeout**. `WAKE_MODE=poll` restores V1.

**Reason:** Idle schedule latency is dominated by poll interval, not skip-locked. Claim, lease, and fence stay on SQL.

**Rejected:** Redis pubsub, Kafka, shrinking production poll to 5 ms (busy-wait).

**Unchanged:** A missed notify cannot stall the fleet; the listen wait returns to `CLAIM_SQL`.

## ADR 3 — At-least-once, not exactly-once

**Decision:** Honest at-least-once attempts. Fenced **row** updates. Idempotent **effects** only if the handler uses `workflow:idempotency_key:step`.

**Reason:** Exactly-once against arbitrary HTTP is a lie. Crash drills show naive `write_count >= 2` and idempotent `write_count == 1`.

## ADR 4 — Fencing token, not a boolean lock

**Decision:** `fencing_token` monotonic per step; complete/heartbeat require it.

**Reason:** `running=true` leaks on `kill -9`. A lease expires. Steal requires an epoch so the dead worker cannot clobber the new one.

## ADR 5 — Database `now()` for leases

**Decision:** SQL `now() + interval`. Workers pass an interval string.

**Reason:** Worker NTP is not a consensus clock. Tests inspect the SQL for `now()`.

## ADR 6 — Python 3.12 (locked)

**Decision:** CPython workers, processes for crash isolation, threads for slots.

**Reason:** Product lock. Cost: GIL, cooperative timeouts, `os._exit` in a handler thread kills the whole process (desired for crash drills).

**Rejected:** Go (original plan default), Celery, Temporal.

## ADR 7 — Linear named steps, not workflow-as-code

**Decision:** Registry of ordered `Step` callables. Current row state, not event-sourced history.

**Reason:** You can `SELECT` the truth. Temporal replay is a different product. Revisit if we need deterministic timers/signals.

## ADR 8 — No auth in V1

**Decision:** Open HTTP locally. Demo kill switch behind `DEMO_MODE`.

**Production would need:** network isolation or mTLS, caller identity, audit, and demo routes disabled.

## ADR 9 — `http_post` host allowlist

**Decision:** Only `ALLOWED_HTTP_HOSTS` (default localhost).

**Reason:** Arbitrary URLs are SSRF. The handler exists to test idempotency headers, not to be an open proxy.
