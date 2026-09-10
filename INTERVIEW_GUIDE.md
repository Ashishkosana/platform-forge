# Interview guide

Defend this codebase, not a blog post. If you cannot point at a file, you do not know it yet.

## 60 seconds

“We built a durable step runner. A job is a linear list of named steps in Postgres. Workers are OS processes that claim a runnable step with `FOR UPDATE SKIP LOCKED`, hold a lease using database `now()`, and increment a fencing token. If the process dies after a side effect, another worker retries the **step**, not the whole job. We are at-least-once. Exactly-once is a handler protocol, which we prove with a naive counter versus an idempotent insert.”

## 5-minute walkthrough

1. Schema: `schema.sql` — `jobs`, `steps`, `attempts`, `lab_effects`.
2. Submit: `claims.submit_job` — unique `(workflow_name, idempotency_key)`, step 1 `pending`, rest `blocked`.
3. Claim: `CLAIM_SQL` in `claims.py`.
4. Execute: `worker.execute_claimed` — heartbeat thread, handler thread, cooperative timeout.
5. Complete: `COMPLETE_SUCCESS_SQL` requires `fencing_token`. Unblock `seq+1` in the same transaction.
6. Failure: `complete_failure` — jittered `run_after` or `dead_lettered`.
7. Evidence: `drills/test_failure_drills.py` and `BENCHMARKS.md`.

## Data model / flow / invariants

See ARCHITECTURE.md. Delivery guarantee: **at-least-once attempts**. Consistency: Postgres read-committed + unique constraints + fencing predicates. When the database is down, workers log errors and do not mark success (bounce drill not run in CI).

## Most important trade-off

Postgres as queue vs a broker. We can inspect and transact. We do not get log retention or independent consumer groups.

## Largest bottleneck (measured)

Sequential HTTP submit + JSONB payload size. Two workers did not double 50-job throughput. 100 KiB input dropped 270 → 153 jobs/s.

## What we did not build

Temporal history/replay, DAGs, Kafka, auth, OTel, compensations, Kubernetes.

---

## 20 beginner questions

Look at the cited path before answering.

1. What tables exist? (`schema.sql`)
2. What does `queued` vs `running` mean on a job? (`models.py`, ARCHITECTURE.md)
3. Why is step 2 `blocked` at insert? (`submit_job`)
4. How does a worker find work? (`CLAIM_SQL`)
5. What is `SKIP LOCKED`?
6. What is a lease vs `status='running'`?
7. Who computes `leased_until`? (`now() + interval`)
8. What is a fencing token?
9. What happens if `POST /jobs` is retried with the same key? (`test_duplicate_submit`)
10. Where is retry backoff defined? (`full_jitter`)
11. When does a step dead-letter? (`attempt_count >= max_attempts`)
12. How do you cancel? (`POST /jobs/{id}/cancel`)
13. How do you replay? (`replay_step`)
14. Does the API run handlers? (no)
15. What is `lab_effects` for?
16. What does `/metrics` expose?
17. What port does the API use? (43180 default)
18. Why Python processes not just threads? (`kill -9` isolation)
19. What is `DEMO_MODE`?
20. Where is the ops console served? (`GET /`)

## 20 intermediate questions

1. Walk through claim + attempt insert in **one transaction**. Why?
2. Why `FOR UPDATE OF s` and not the join of `jobs`?
3. Heartbeat in another thread: why a separate connection? (`worker.py` comment / `db.py` pool)
4. Timeout: what if the handler thread is still alive? (cooperative; stop heartbeat)
5. Cancel vs in-flight `running` step: which rows does `cancel_job` update?
6. How is the next step unblocked atomically with success?
7. Why not include `fencing_token` in the effect idempotency key? (`handlers` / V1.md)
8. Naive vs idempotent SQL on `lab_effects`.
9. Why crash handlers only `_exit` on attempt 1?
10. How would you scrape worker counters vs API gauges?
11. What does `stuck=true` mean?
12. Why full jitter instead of `base * 2^n`?
13. GIL: when do you add processes vs `--concurrency`?
14. How does `http_post` avoid SSRF?
15. What happens if complete_success returns 0 rows after a successful HTTP call?
16. Why is OpenTelemetry out of V1?
17. How do tests start a real worker? (`start_worker`)
18. Why `stdout=DEVNULL` on workers in tests?
19. Interpret the 100 KiB bench vs 64 B.
20. What production auth would you add without changing the state machine?

## 20 adversarial / senior questions

1. Prove you are not exactly-once. Cite a test where `write_count >= 2`.
2. Two workers in one step: can both perform HTTP POST? Yes. What stops double state commit?
3. If we `SIGSTOP` a worker, why does the lease expire if the process is “alive”?
4. Could a heartbeat with a stale token extend a stolen lease? Show the WHERE clause.
5. Is `SKIP LOCKED` fair? Can a job starve?
6. Read-committed: can cancel race a claim? What is the residual failure?
7. If attempt insert fails after the claim UPDATE, what happens? (`ON CONFLICT DO NOTHING`, same txn — they commit together)
8. Why is 2PC the wrong answer for `http_post`?
9. When would you introduce an outbox?
10. Why is Celery the wrong comparison in an interview?
11. Why is Temporal the right comparison, and what did we refuse to copy?
12. How would you make handler timeout preemptive? (separate process per attempt — cost)
13. Postgres bounce: what is unverified in CI and why is that honest?
14. Metrics lie if you only scrape the API. How do you say that without sounding sloppy?
15. 2 workers ≈ same jobs/s as 1. Defend “so we need Kafka” as a bad leap.
16. Clock skew across workers: what still works? What does not (timeouts)?
17. Could `lab_effects` uniqueness be the product store? Why must it not?
18. Demo `/demo/workers/kill` on a public IP: what is the incident?
19. How do you evolve to DAGs without rewriting history?
20. What measurement would make *you* abandon skip-locked?

Do not memorize answers. Re-run `pytest drills/ -q` the night before.
