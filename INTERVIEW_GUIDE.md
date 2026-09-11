# Interview guide — Platform Forge

Defend this codebase, not a blog post. If you cannot point at a file, you do not know it yet.

## 30-second explanation (recruiter)

I built a job engine in Python and Postgres. You submit a linear workflow. Separate worker processes pick up one step at a time with a database lock, hold a time-limited lease, and finish only if they still hold the current fencing token. If a worker dies after doing the side effect, another worker retries that step — not the whole job. We are honest about at-least-once: duplicate attempts are expected; duplicate charges are the handler’s problem, and we have a lab that shows both.

## 2-minute technical explanation

**Problem.** A worker can complete an HTTP call or a SQL write and then die before it records success. Retrying the whole pipeline double-charges. Hiding that behind “exactly-once” is a lie against arbitrary side effects.

**Architecture.** FastAPI inserts `jobs` and `steps`. Workers are OS processes. Claim SQL is `SELECT … FOR UPDATE SKIP LOCKED` in `claims.py`. The lease deadline is `now() + interval` in Postgres, not the worker’s clock. Each claim increments `fencing_token`. Heartbeat and complete use `WHERE fencing_token = $token AND worker_id = $me`. V2 wakes idle workers with `NOTIFY workflow_wake`; poll interval is the timeout fallback. Claim path is unchanged.

**Hardest challenge.** Two workers overlapping one step after lease expiry: both may perform the effect; only the current token may write step state. Cancel must win over a late handler success. Heartbeat must not treat one blip as lease loss.

**Solution.** Monotonic fence; complete-success requires the job still `queued`/`running`; demo signals only API-spawned PIDs; crash lab handlers exit only on attempt 1 so recovery can finish (documented concession).

**Evidence.** `drills/test_failure_drills.py`: naive `write_count >= 2`, idempotent `== 1`, zombie `SIGSTOP` + fence reject. Benches in `BENCHMARKS.md` (50 noop jobs, this VM, sequential submit).

## Architecture walkthrough

1. **Submit** (`claims.submit_job`): unique `(workflow_name, idempotency_key)`. Step 1 `pending`, later steps `blocked`. `NOTIFY workflow_wake`.
2. **Claim** (`CLAIM_SQL`): pending with `run_after <= now()`, or running with `leased_until < now()`. Increments fence and `attempt_count`, inserts `attempts` in the same transaction.
3. **Execute** (`worker.execute_claimed`): heartbeat thread on its own connection; handler on a worker slot thread; cooperative timeout (stop heartbeat, do not kill the thread).
4. **Success** (`COMPLETE_SUCCESS_SQL`): matching fence. Unblock `seq+1` in the same transaction. Notify again so the next step does not wait a full poll.
5. **Failure** (`complete_failure`): `pending` + jittered `run_after`, or `dead_lettered` after `max_attempts`.
6. **Steal:** another process claims the expired running row with fence N+1. Stale complete of fence N updates 0 rows (`rejected_fence`).
7. **Inspect:** `GET /jobs/{id}` is the state dump. `/metrics` is Prometheus text; gauges are SQL, counters are per process.

## Important concepts (this repo)

**At-least-once vs exactly-once.** The engine retries steps. That is at-least-once *attempts*. Exactly-once *effects* need an idempotency key in the handler (or in the downstream). We prove both with `lab_effects`.

**Lease.** `status='running'` is not a lock. `leased_until` is. After expiry, the row is claimable again.

**Fencing token.** An epoch. Steal increments it. Old heartbeats and completes do not match `WHERE fencing_token = …`.

**SKIP LOCKED.** Waiters skip rows other sessions already locked, so workers do not queue behind a stuck claimer. It is not a fairness guarantee; a job can wait if others keep winning the scan.

**Database `now()`.** Worker NTP is not consensus. Lease math is SQL.

**Full jitter.** `U(0, min(max_backoff, base * 2^(attempt-1)))` in `registry.py`. Avoids a retry herd sharing one wake-up.

**LISTEN/NOTIFY (V2).** Wake, not a log. Missed notify cannot stall the fleet because listen wait times out into the same claim loop. `WAKE_MODE=poll` disables it.

**Linear steps, current row.** Truth is the `steps` row, not an event-sourced history. No DAG, signals, or Temporal replay.

## Design decisions

See [DECISIONS.md](DECISIONS.md). Short form:

- Postgres as queue so claim, attempt insert, and fence live in one transaction.
- Processes not only threads so `kill -9` isolates a slot.
- Cooperative timeout because CPython cannot kill a stuck thread.
- No broker in V1: a second system would dual-write with the step table.
- Lab crash handlers `_exit` only on attempt 1 — otherwise recovery livelocks. Production handlers must be idempotent; they must not be designed to abort the process.

## Tradeoffs

| Choice | Alternative | Why this repo |
| --- | --- | --- |
| Postgres `SKIP LOCKED` | Redis/SQS/Kafka + DB | One durability story; inspect with `SELECT` |
| LISTEN as wake | Always poll, or Redis pubsub | Idle latency without a new datastore; poll remains correct under load |
| Fencing token | Boolean `locked` | `kill -9` leaves `running=true` |
| Linear registry | Workflow-as-code / DAG | You can read the current row; Temporal is a different product |
| Threads as slots | Process per attempt | Cheaper concurrency; timeout is cooperative |
| At-least-once | 2PC / outbox-as-exactly-once | 2PC does not recall HTTP that already left |

Revisit the queue if skip-locked saturates while CPUs are idle (not seen at 50 noop jobs). Revisit payload-on-row if 100 KiB inputs become normal (bench: 270 → 153 jobs/s).

## Failure scenarios

| What happens | What the system does | What it does not do |
| --- | --- | --- |
| Worker `kill -9` after naive increment | Lease expires, steal, second attempt, `write_count >= 2` | Un-write the first increment |
| Same, idempotent handler | Second attempt, `write_count == 1` | Claim exactly-once engine semantics |
| `SIGSTOP` zombie | Heartbeats stop, lease expires, other worker runs, `SIGCONT` complete is fenced out | Freeze the lease because the process is “alive” |
| Duplicate `POST /jobs` | Same job id (unique key) | Two pipelines |
| Poison `always_fail` | Dead-letter after max attempts; other jobs proceed | Block the queue behind the poison |
| Cancel vs late success | Cancel wins; running steps flipped; success SQL requires job not cancelled | Wait for the handler thread to notice |
| Handler timeout | Cooperative; heartbeat stops | Preempt the stuck thread |
| Missed NOTIFY | Poll timeout on the listen wait | Stall until a human restarts workers |
| Postgres down | Workers log, do not mark success | CI-measured bounce (gated, not run here) |
| Scrape only API `/metrics` | Gauges from SQL still work | Worker counters are undercounted |

## Limitations

- Not exactly-once. Not Temporal. Not a DAG engine.
- No auth. Demo kill routes are lab-only.
- Not a production capacity rating. 50 jobs, one VM, sequential submit.
- Postgres bounce **not measured** in CI.
- 1000-job retry herd **not measured** (40 used).
- Dedicated steal-latency timer **not measured** (implied by test TTL of 2s).

## Interview questions (with answers from this tree)

1. **What is the delivery guarantee?** At-least-once *attempts*. Cite `FAILURE_DRILLS.md` naive `write_count >= 2`. Exactly-once is a handler protocol (`crash_idempotent` keeps `write_count == 1`).

2. **Does the API run handlers?** No. `python -m workflow_engine.api` inserts and inspects. `python -m workflow_engine.worker` runs handlers.

3. **How does a worker find work?** `CLAIM_SQL` in `claims.py`: `FOR UPDATE SKIP LOCKED` on claimable pending or expired running steps.

4. **Who sets `leased_until`?** SQL `now() + interval`. Tests assert the SQL contains `now()` (`test_clock_skew_sql_uses_now`).

5. **What is a fencing token?** Monotonic integer per step, incremented on claim. Heartbeat and complete require it. Stale worker gets 0 rows.

6. **Why can two workers run the same step?** Leases expire. Overlap is expected. Fence protects *state*, not the external call.

7. **Why `SKIP LOCKED` instead of waiting?** So a worker does not block on a row another worker already locked. Fairness is not promised.

8. **What happens on duplicate submit?** Unique `(workflow_name, idempotency_key)`. `test_concurrent_duplicate_submit`: 50 parallel POSTs, one job.

9. **How is the next step released?** Success complete unblocks `seq+1` in the same transaction. Step 2 starts `blocked`.

10. **Retry shape?** Full jitter in `registry.py`. After `max_attempts`, `dead_lettered`. Replay resets `attempt_count` for that step.

11. **Cancel vs in-flight success?** `complete_success` requires `jobs.status IN ('queued','running')`. Cancel flips running steps. Late handler cannot mark succeeded.

12. **Why processes, not only threads?** `kill -9` isolation. `os._exit` in a handler thread kills that process — desired for crash drills.

13. **Why is timeout cooperative?** CPython cannot kill a stuck thread. We stop heartbeating so the lease can expire.

14. **LISTEN vs poll?** Default `WAKE_MODE=listen`. Claim path unchanged. Poll timeout on `notifies(...)` so a dropped notify cannot stall. Benches used 50 ms poll and a pre-queued batch — they do **not** prove LISTEN unnecessary for idle wait.

15. **Why not Kafka/Celery/Temporal?** Celery is a broker + at-least-once with less step state. Temporal is the honest comparison (history, workflows-as-code) and is a different product. Kafka does not give us claim+fence in one SQL transaction.

16. **Is `http_post` an open proxy?** No. `ALLOWED_HTTP_HOSTS`, http(s) only. SSRF note in `SECURITY.md`.

17. **What is `stuck`?** Jobs still `queued`/`running` that look overdue — not cancelled or dead-lettered leftovers. Filter was fixed after cancel left running steps stranded.

18. **How do you scrape metrics honestly?** `/metrics` gauges are SQL snapshots. Counters are per process. Durable truth: `attempts` table.

19. **What would make you abandon skip-locked?** Claim latency or lock wait saturating while CPUs are idle. Not observed at n=50.

20. **Demo `/demo/workers/kill` on a public IP?** Incident. Only enable `DEMO_MODE` locally; signals are limited to API-spawned PIDs; default is `false`.

### More practice prompts (look at the file first)

Beginner: tables in `schema.sql`; `queued` vs `running`; why step 2 is `blocked`; `lab_effects`; port **43180**.

Intermediate: claim + attempt insert in one transaction; heartbeat’s extra connection; why the effect key must not include `fencing_token` (steal would miss the first write); GIL vs `--concurrency`; 100 KiB bench vs 64 B.

Adversarial: is skip-locked fair; read-committed cancel vs claim; why 2PC is the wrong answer for `http_post`; when an outbox appears; clock skew vs SQL `now()`.

Re-run `pytest drills/ -q` the night before.
