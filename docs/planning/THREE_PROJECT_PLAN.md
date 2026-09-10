# Three Project Plan

This is the planning artifact. It is not an architecture dump and it is not a license to generate three codebases.

**Status:** One git remote per system. **This repository is Project 1 only.** Project 2 (`ai-reliability-control-plane`) and Project 3 (`realtime-event-platform`) are separate remotes — never folders in this tree. Language for Project 1 is locked to **Python + Postgres**. V1 design: `docs/planning/V1.md`.

---

## How we will work

The same loop for every project:

1. **V1 is a laboratory**, not a product. It must run, persist, fail, recover, and emit numbers.
2. **V2 is a reaction.** It exists only where V1's benches or drills prove a specific gap.
3. **Interview defense is a first-class output.** Each project lists the concepts you must be able to teach from the code, not from a blog post.

Shared bar for every V1:

- One durable store you can inspect with SQL (or an equivalent explicit log).
- Crash the process mid-work and show what is recovered vs duplicated.
- Metrics + structured logs with a correlation id from ingress to side effect.
- A README path: submit work → watch it → kill a worker → watch recovery.
- No extra moving parts until a number or a drill demands them.

Shared non-goals (all three, V1):

- Multi-region
- Kubernetes
- Custom consensus
- Auth productization
- Pretty dashboards as a substitute for traces and counters

Recommended language split — justified, not decorative:

| Project | Default | Why this, not the other |
| --- | --- | --- |
| 1. Workflow engine | **Python + Postgres** (locked) | Same durability story as the Go default: leases, `SKIP LOCKED`, crash recovery. Python is a product constraint, not a fashion choice; we pay with the GIL and cooperative timeouts (see V1.md). |
| 2. AI control plane | Python | The domain objects are traces, evals, token accounting, and provider SDKs. Fighting that in Go is fashion. |
| 3. Event / notification | Go + Postgres | Same durability toolkit as Project 1, plus connections (WebSocket/SSE). Reuse mental models; do not invent a broker yet. |

Project 1 is locked to Python. If you want a single language across all three, stay on Python + Postgres and accept that Project 3 will not get Go's connection model. Do not introduce a third stack.

---

## Project 1 — Distributed workflow / job execution engine

### Problem statement

Product systems accumulate “do these steps, in order, and don't lie about it”: provision a tenant, charge then email then provision, transcode then notify, run a CI pipeline. The naive design is a queue plus a retry loop around the **whole** job.

That design fails in a specific, boring way:

- Step 2 succeeded (money moved, email sent) and step 3 failed. Retrying the job repeats step 2.
- The process dies after the side effect and before the ack. The job is either lost or duplicated.
- Nobody can answer “where is this stuck?” without grepping logs.
- Poison jobs retry forever and starve healthy work.
- Two workers run the same step because a lock was a boolean, not a lease with a heartbeat.

The problem is **durable, observable execution of multi-step work** with:

- Per-step state (not just job-level status)
- At-least-once execution **and** a story for idempotent effects
- Recovery when a worker vanishes
- Explicit cancel, timeout, and dead-letter

This is not “build Temporal.” Temporal's interesting ideas (history, replay, deterministic workflow code) are **V2 candidates**, and only if V1's model of “row per step” actually breaks.

### V1 scope

A **durable step runner** shared by N worker processes.

In:

- Job submit API: name, input JSON, idempotency key
- A **registered** workflow: an ordered list of named steps (linear pipeline). No DAG yet unless a drill requires fan-out/fan-in.
- Workers that **claim** a runnable step with a lease, heartbeat, execute a handler, record success/fail

Out of V1:

- Workflow-as-code with replay
- Child workflows, signals, external events (“wait for human”)
- Cron/timers as a cluster feature beyond `run_after`
- Exactly-once side effects without handler cooperation
- Sharding, Kafka, Redis as the source of truth

Concrete V1 surface:

1. **Postgres** as source of truth: `jobs`, `steps`, `attempts`. Queue = `SELECT … FOR UPDATE SKIP LOCKED` on runnable steps whose lease is null or expired.
2. **Lease + heartbeat.** Claiming is not `status = 'running'`. Claiming is `leased_until = now() + T`, `worker_id = me`. If heartbeats stop, another worker may claim.
3. **Retry policy** per step: max attempts, exponential backoff with jitter, dead-letter after max.
4. **Idempotency key** on submit. Duplicate submit returns the existing job, does not create a second.
5. **Handlers are functions** registered in-process. V1 does not ship a plugin ABI. Include 3–4 real-ish handlers (HTTP call with idempotency header, DB write, a deliberately flaky step) so drills are not `sleep(1)`.
6. **Admin API / CLI:** submit, get job, list stuck jobs (lease expired or attempt age), cancel, replay a dead-lettered step **explicitly**.
7. **Crash test harness:** start workers, submit N jobs, `kill -9` a worker, assert: no job vanished; in-flight steps either completed or were reclaimed; duplicate side effects are counted (not wished away).

Success for V1: you can draw the step state machine on a whiteboard and it matches the rows in Postgres after a kill.

### Trade-offs (and the V1 choice)

| Decision | Options | V1 choice | Why | Revisit when |
| --- | --- | --- | --- | --- |
| Queue | Postgres skip-locked vs Redis/SQS vs Kafka | Postgres | One system to inspect; transactional claim + state write | Claim latency or lock contention saturates under the bench |
| Work shape | Linear vs DAG vs free-form code | Linear named steps | Forces per-step state without workflow-as-code complexity | A real job needs fan-out/fan-in **and** V1 encoding is painful |
| Execution model | DB state machine vs event-sourced history | Rows for job/step/attempt | You can `SELECT` the truth. Replay is optional later | You need deterministic replay, timers, or “why did it do that” from a history log |
| Worker communication | Poll vs listen/notify vs push | Poll with backoff + optional `LISTEN` | Poll is correct under load; LISTEN is a latency optimization | p99 schedule latency is the SLO and poll interval is the cause |
| Exactly-once | 2PC vs outbox vs idempotent handlers | At-least-once + idempotency keys on effects | Honest. 2PC against arbitrary HTTP is a lie | A handler cannot be made idempotent and duplicates are costly |
| Language runtime | Threads vs processes vs remote executors | Python **OS processes** + optional in-process slots | Distribution = shared DB + leases; `kill -9` is per process | CPU isolation needs more processes (GIL); remote executors if handlers must be sandboxed |

### Failure modes V1 must demonstrate

These are drills, not slides. Each gets a script and an expected invariant.

1. **Worker `kill -9` after side effect, before status write.** Expect: step retried; handler sees the same idempotency key; duplicate *attempts* ≥ 1; business effect 0 or 1 depending on handler idempotency. Measure both a naive handler and an idempotent one.
2. **Lease timeout while handler still running (zombie).** Two workers in the step. Expect: we detect overlapping execution (fencing token / attempt id) and the late writer cannot clobber a newer attempt.
3. **Poison step.** Always fails. Expect: bounded retries, then `dead_lettered`; other jobs continue; queue depth of healthy work does not grow without bound.
4. **Retry thundering herd.** 1,000 jobs fail at once (dependency down). Expect: jittered backoff; we can show a flattened retry histogram vs a spike. If V1 has no jitter, the drill fails the design — add jitter in V1, do not wait for V2.
5. **Postgres briefly unavailable.** Expect: workers error, do not fake success; jobs remain; recovery without manual repair.
6. **Cancel during run.** Expect: in-flight attempt finishes or is aborted at a heartbeat check; no new attempts; terminal state `cancelled`.
7. **Duplicate submit.** Same idempotency key, concurrent. Expect: one job row.
8. **Clock skew on `leased_until`.** Document that V1 trusts DB time (`now()`), not wall clocks of workers. Drill: worker with wrong clock still cannot extend leases incorrectly if SQL uses `now()`.

### Observability plan

If you cannot answer these from telemetry, V1 is incomplete:

- How many runnable steps, running (valid lease), blocked-on-backoff, dead-lettered?
- Age of the oldest runnable step (scheduling lag).
- Lease expirations per minute (this is your silent crash detector).
- Attempts per job, success/fail by step name.
- Handler latency p50/p95/p99 by step name.
- Overlapping-execution detections (fencing violations).

Implementation bar (V1, not a vendor):

- Structured logs: `job_id`, `step_id`, `attempt`, `lease_id` / fencing token, `worker_id`.
- Prometheus counters/histograms **or** an equivalent scraped `/metrics`.
- One OpenTelemetry trace per job, span per attempt. Optional in V1 if metrics + logs are excellent; do not add OTel as decoration.
- `GET /jobs/:id` returns the state machine, not a log dump.

Alert ideas you should be able to defend: lease expiration rate, dead-letter rate, oldest runnable age, overlap detections > 0.

### Benchmarks

Run with a documented machine shape (N workers, Postgres local, handler = no-op vs 50ms HTTP stub).

| Bench | What we measure | What would force V2 discussion |
| --- | --- | --- |
| Throughput | Completed no-op jobs/sec vs worker count | Postgres skip-locked saturates while CPUs are idle |
| Schedule latency | Submit → first attempt start, p50/p99 | p99 ≫ poll interval; justify LISTEN or push |
| Recovery | Time from `kill -9` to reclaim of that worker's leases | Recovery ≫ lease TTL; heartbeat/TTL wrong |
| Contention | Many workers, few runnable steps | High lock wait, wasted CPU — claim query shape is wrong |
| Durability | Kill mid-commit; count lost jobs (must be 0) | Lost jobs = bug, not a V2 feature |
| Retry load | Failures injected at 10%/50% | Retry traffic > useful traffic without jitter/limits |
| Payload size | 1KB vs 100KB inputs | Bloated rows / TOAST; move payloads off the claim path |

Write numbers in `projects/workflow-engine/BENCHMARKS.md`. No numbers, no V2.

### Interview concepts this project exists to teach

You should be able to teach these from **this** code:

- At-least-once vs exactly-once vs effectively-once (idempotent effects)
- Leases vs locks; why a boolean `locked` is not enough
- Fencing tokens / epoch on steal
- `SKIP LOCKED` as a work queue; what it does not give you (fairness, delay queues)
- Sagas vs 2PC; why compensating actions are not in V1
- Poison queues and retry jitter
- Why Temporal stores **history** and replays, and why V1 does not
- Outbox pattern (candidate when a step must publish an event atomically with state)
- Idempotency keys as a protocol, not a flag

V2 candidates (not a roadmap — a list of *if*):

- **If** operators need “why” and time-travel: append-only attempt/history log as the source of truth, current state as a projection.
- **If** skip-locked saturates: dedicated queue for runnable step ids, Postgres remains system of record.
- **If** we need sleep-for-hours / signals: timers and external completions as first-class events, not `sleep` in a worker.
- **If** we need DAGs: explicit child steps + join counter, still not a workflow DSL.

---

## Project 2 — AI reliability control plane (not a chatbot)

### Problem statement

LLM features fail in production in ways a chat UI hides:

- Cost is unbounded per tenant/feature because every call is “just one more completion.”
- Latency SLOs are missed and the app cannot degrade — it only retries.
- A prompt or model swap ships, quality drops, and nobody can prove when or why.
- Tool/function calls loop, or the model succeeds and the tool fails, and the trace is a stdout line.
- There is no kill switch, no budget, no eval gate, and no record of which prompt **version** produced a given output.

The product is a **control plane around model calls**: policy, routing, budgets, tracing, eval gates, and degrade paths.

It is **not**:

- A chatbot
- A RAG app
- An agent framework
- A wrapper whose only feature is “we called OpenAI for you”

If the demo requires a chat window to look impressive, the project has already failed its brief.

### V1 scope

A **synchronous control plane** applications call instead of calling the provider SDK directly.

Caller:

```text
ControlPlane.complete(feature, tenant, input, timeout)
```

V1 must do, in order, on the hot path:

1. Load **feature config**: prompt version, model, max tokens, timeout, fallback model, kill switch.
2. **Budget check** (tenant × feature): remaining tokens or USD for a window. Fail closed or degrade — chosen per feature, explicit.
3. **Emit a trace id.** Persist a span: feature, prompt version hash, model, input hash, token counts, cost, latency, status. Store raw payloads behind a flag; default to hash + truncated preview so the control plane does not become a PII warehouse by accident.
4. Call the provider (real SDK). Fake provider available for benches and drills.
5. On provider failure / timeout: retry **once** only if the error is classified retryable; otherwise fallback model if configured; otherwise error. No unbounded retry on the hot path.
6. Record usage against budget (atomically enough that concurrent requests cannot silently 10× the cap — see drills).
7. Return result + trace id to the caller.

Offline path (still V1, not a chatbot):

- **Prompt registry:** immutable versions. “Deploy” means pointing a feature at a version.
- **Golden eval set** per feature: input → expected properties (exact match, contains, JSON schema, or a cheap checker). Run evals on a candidate version. **Refuse to promote** if pass rate < threshold.
- CLI: `eval run`, `feature promote`, `feature kill`, `budget show`.

Out of V1:

- Multi-agent planners
- Vector search / memory
- Fine-tuning
- Semantic cache (popular, usually wrong until you measure duplicate rate)
- Human-in-the-loop review queues
- “LLM as judge” as the only eval (too slow/expensive/circular for a gate). Optional **sampled** judge later; V1 gates on deterministic checkers plus a tiny labeled set.

Success for V1: you can block a bad prompt version, cap spend under concurrent load, and reconstruct a failed production call from a trace id — with no chat UI.

### Trade-offs (and the V1 choice)

| Decision | Options | V1 choice | Why | Revisit when |
| --- | --- | --- | --- | --- |
| Insertion point | Library vs sidecar/proxy | In-process library + HTTP adapter | Lowest latency lie; proxy is for polyglot later | A second language appears or you need a kill switch without deploys |
| Fail open vs closed | If control plane store is down | **Per feature**, default fail closed for paid/expensive features | Fail open is how bills explode | Availability SLO of the app exceeds cost SLO and you can prove it |
| Budget accounting | Redis INCR vs SQL vs provider bills | SQL transaction or single-row atomic increment | Correctness first; Redis if the bench shows contention | Hot feature × tenant saturates row updates |
| Eval | Online vs offline vs both | Offline gate on promote + sampled online logging | Online eval without a gate is a dashboard that nobody blocks on | Drift appears **after** promote in production traces |
| Payload retention | Full vs hash | Hash + optional full on debug tenants | Traces that store prompts become a compliance incident | Debugging a class of failures requires full text |
| Provider abstraction | One vendor vs router | One real + one fake in V1 | Multi-provider routing is V2 unless a drill shows we need degrade across vendors | Primary provider SLO is the actual outage mode |

### Failure modes V1 must demonstrate

1. **Control plane store down.** Fail-closed feature returns error, does not call provider (no unmetered calls). Fail-open feature (if any) calls provider and logs `unmetered=true`.
2. **Budget race.** 100 concurrent requests, remaining budget = 10 requests. Expect completions ≤ 10 + documented slack. If 80 complete, the budget is a suggestion — fix in V1.
3. **Provider timeout vs client timeout.** Caller timeout 2s, provider would return at 5s. Expect: caller gets error, in-flight call is cancelled or spanned as `cancelled`, budget/tokens accounted honestly (you didn't “succeed”).
4. **Fallback storm.** Primary fails 100%. Expect: fallback used, but fallback has its own budget/limit; no recursive retry loop.
5. **Kill switch.** Flip feature off; in-flight calls finish or cancel at next check; new calls fail fast with a stable error code.
6. **Bad promote blocked.** Candidate version fails golden set; `promote` refused; live feature still on previous version.
7. **Silent quality regression.** Change a prompt, eval still passes because the checker is weak. This drill is supposed to **hurt**: it teaches that eval coverage is the product. Document the miss; add one checker that would have caught it. Do not “fix” it by adding an LLM judge as a default.
8. **Trace write failure after provider success.** Caller still gets the model result (or not — pick one and defend it). Trace loss is visible as a metric. Do not dual-write yourself into losing user responses.

### Observability plan

The control plane **is** an observability product. Minimum:

Per call: trace id, feature, tenant, prompt version, model, retry/fallback used, tokens in/out, estimated USD, latency, error class (`timeout`, `rate_limit`, `policy`, `budget`, `provider_5xx`).

Aggregates:

- Spend per feature/tenant over the budget window
- Error rate by class
- p50/p95/p99 latency **including** control-plane overhead vs provider time (split the span)
- Eval pass rate of the **currently live** version (from the last eval run, timestamped — stale evals should look stale)
- Unmetered call count (fail-open)
- Promote events (who, from version, to version, eval id)

If overhead p99 is not measured, you cannot claim the proxy is “cheap.”

### Benchmarks

| Bench | What we measure | What would force V2 discussion |
| --- | --- | --- |
| Overhead | p50/p99 added latency vs raw SDK, fake provider | Overhead dominates; drop payload writes from hot path or move to async |
| Trace write | sustained completes/sec | Sync insert saturates — async append + loss policy |
| Budget correctness | concurrent remaining-budget test | Need a faster atomic store |
| Eval runtime | golden set of 100 / 1,000 cases | Slow gate → people bypass it; shard or sample, don't weaken |
| Kill switch | time-to-effect after flip | If config is cached too long, the switch is fake |
| Cost accuracy | summed traces vs mocked price table | Drift means finance cannot use this — fix accounting |

Provider bills in real life will not match traces exactly (cached tokens, rounding). V1 documents the reconciliation error; it does not pretend to be the invoice.

### Interview concepts this project exists to teach

- SLOs for **probabilistic** systems: availability, latency, cost, and quality are four different numbers
- Offline eval as a release gate vs online monitoring
- Why “accuracy” is usually an undefined metric
- Fail open vs fail closed when the **safety** system is on the hot path (same shape as authz)
- Budget as a distributed counter; lost updates
- Error classification (retryable vs not) and retry amplification
- Prompt versioning as config, not as strings in application code
- Shadow/canary of a prompt version (V2 candidate)
- Why semantic cache is a correctness hazard (same prompt, different user context)
- Data retention / PII in traces

V2 candidates (only if measured):

- **If** duplicate inputs are actually common: cache by `(feature, prompt_version, input_hash)` with TTL — never by “semantic similarity” first.
- **If** one provider is the outage: model routing with explicit degrade SLOs.
- **If** deterministic evals miss product failures: sampled human or judge review, **in addition to** the gate, never as a replacement.
- **If** polyglot callers: sidecar proxy with the same trace schema.

---

## Project 3 — Real-time event and notification platform

### Problem statement

Products need to tell humans and other systems that something happened: in-app inbox, live client, webhook, later email/push. The naive design is `INSERT INTO notifications` plus the request handler also writing to a socket, plus a cron for email.

That design fails in a specific, boring way:

- The DB write commits, the process dies, the live client never sees it, and there is no cursor to catch up.
- One event becomes 10 million notifications (broadcast) and the write path falls over.
- A slow webhook subscriber stalls the worker pool; everyone's push is late.
- Reconnects replay nothing or replay everything.
- The user muted “marketing” and still gets the email because preference was checked in the wrong layer.
- “Sent” means “we called `send()`,” not “the other side accepted it.”

The problem is a **pipeline**: ingest an event once → decide recipients → persist an inbox item → deliver to live subscribers and async channels → retry with backoff → honor preferences → expose catch-up.

This is not “build Kafka + Flink + a notifications SaaS.” Kafka is a V2 candidate if **retention + independent consumer groups** become the measured need.

### V1 scope

A **single-region notification pipeline** with two delivery paths: durable inbox + live catch-up.

In:

1. **Publish API:** `event_type`, `tenant`, `payload`, **idempotency key**, optional `recipients` (explicit list in V1; not “query the whole user table”).
2. **Persist the event** once. Fan-out **writes** inbox rows for recipients (write fan-out, bounded — V1 recipient lists are modest; see benches before pretending to be Twitter).
3. **Preferences:** per user, mute an `event_type`. Checked **before** inbox insert and before async send.
4. **Live delivery:** WebSocket or SSE. Client sends `last_event_id` (or last inbox id) on connect; server replays the gap then streams. Polling-only is not enough for the brief; **unreliable** sockets with no cursor are also not enough.
5. **Async channel:** webhook worker with at-least-once delivery, signed payload, retry/backoff, dead-letter. Email/push are **adapters with a fake transport** so drills do not need APNs certs.
6. **Outbox-ish coupling:** creating inbox rows and enqueueing deliveries happens in the **same transaction** as the event insert, or via a transactional outbox table. No “best-effort second write.”

Out of V1:

- Global broadcast to all users as a primary API
- Cross-region
- Kafka/NATS/Rabbit as the source of truth
- Smart coalescing/digests (10 likes → 1 notification) — V2 if the bench shows hot conversations
- True push providers

Success for V1: kill the API process after commit and before live send; client reconnects with a cursor and converges. Kill a webhook worker; delivery retries; duplicates are possible and **detectable** by the receiver via event id.

### Trade-offs (and the V1 choice)

| Decision | Options | V1 choice | Why | Revisit when |
| --- | --- | --- | --- | --- |
| Live transport | WS vs SSE vs long-poll | SSE **or** WS, one of them, with cursor | SSE is simpler operationally (HTTP, no ping protocol); WS if you already need bidirectional | Browser constraints or bidirectional control messages |
| Broker | Postgres vs Redis pubsub vs Kafka | Postgres for truth; live fan-out via LISTEN/NOTIFY or polling the inbox | One durability story | NOTIFY payload limits / connection count / replay needs force a log |
| Fan-out | Write-time vs read-time | Write-time for explicit recipient lists | Inbox query stays simple; unread count is a row count | Recipient sets are huge; then read-time fan-out or fan-out service |
| Ordering | Per-user vs per-topic vs none | Per-user inbox id monotonic | Users notice inverted inbox more than global order | Multi-device with exact same order requirements |
| Delivery guarantee | At-most vs at-least vs exactly | At-least-once + event id idempotency | Matches reality of HTTP webhooks | A consumer cannot dedup and duplicates are expensive |
| Coalescing | None vs windowed merge | None in V1 | Coalescing hides events; add only with a defined merge key | Hot keys make inbox unusable |

### Failure modes V1 must demonstrate

1. **Process crash after durable write, before live push.** Reconnect + cursor delivers the missed item. Invariant: no silent drop.
2. **Duplicate publish.** Same idempotency key. One event, one inbox row per recipient.
3. **Slow webhook.** One subscriber sleeps 30s. Expect: that subscriber's lag grows; **other** subscribers and in-app delivery do not stall (separate concurrency / per-subscription backoff). If one slow hook stalls the pool, V1 is wrong.
4. **Poison webhook.** 500 forever. Expect: dead-letter, not infinite tight retry.
5. **Reconnect storm.** 10k clients reconnect, each asking for catch-up. Expect: bounded DB load (cap replay window, don’t table-scan). Document the cap.
6. **Preference race.** User mutes while a fan-out is in flight. Document the race. Pick “may deliver one extra” or “lock preference row” and defend it.
7. **Hot recipient list.** 1 event × 50k recipients. Expect: publish latency and DB write time; this is the drill that tells you write fan-out’s ceiling. Do **not** “fix” with Kafka before you have this number.
8. **Client cursor too old.** Replay beyond retention. Expect: explicit error (`cursor_expired`) and a recovery path (full inbox snapshot), not a quiet gap.

### Observability plan

- Ingest rate, unique vs duplicate publishes
- Fan-out amplification (inbox rows per event)
- Inbox write latency, live push latency (commit → client receive) p50/p99
- Connected client count, catch-up replay size
- Webhook success/fail, attempt count, per-subscription lag (oldest undelivered)
- Dead-letter count
- Preference-drop count (events not delivered because muted) — so mute is visible, not a black hole

“Sent” in logs means nothing unless it is tagged `channel`, `attempt`, `destination_status`.

### Benchmarks

| Bench | What we measure | What would force V2 discussion |
| --- | --- | --- |
| Ingest | publishes/sec with tiny fan-out | CPU idle, DB wait → write path / indexes |
| Fan-out | 1 → 1k / 10k / 50k inbox writes, time to durable | Write fan-out is the bottleneck → chunked workers or read fan-out |
| Live latency | commit → SSE/WS receive on a quiet system | If p99 is huge, LISTEN vs poll is the issue |
| Catch-up | 1k events replay on connect | Replay query shape; need a log partitioned by user |
| Isolation | slow webhook vs live p99 | Shared worker pool — split queues |
| Durability | kill mid-fan-out | Partial recipient lists: either transactional all-or-nothing **or** resumable fan-out with a cursor. Pick one and test it. |

Resumable fan-out is the likely first V2 **if** all-or-nothing transactions choke at 50k recipient inserts.

### Interview concepts this project exists to teach

- Write fan-out vs read fan-out (Twitter problem) with **your** numbers
- Transactional outbox: why dual-write to “DB and socket” loses messages
- At-least-once delivery + consumer idempotency
- Cursors, gaps, and retention
- Backpressure and noisy-neighbor subscribers
- SSE vs WebSocket (and why the hard part is catch-up, not the frame type)
- Head-of-line blocking in a shared worker pool
- Notification preference as a **safety** check, not a UI toggle
- When Kafka is justified (replay, many independent consumers, long retention) vs a bigger Postgres

V2 candidates (only if measured):

- **If** fan-out transactions die at large N: chunked, resumable fan-out workers.
- **If** many independent consumers need the same event log: extracted log (Kafka or not) with inbox as a consumer.
- **If** inboxes are chatty: coalescing with an explicit merge key and a flush timer.
- **If** NOTIFY or connection count breaks: dedicated live pubsub, Postgres still owns inbox.

---

## Sequencing

Do not start three labs at once. Skills compound:

1. **Project 1 first** (recommended). Leases, idempotency, skip-locked, crash drills. This is the vocabulary Project 3 reuses.
2. **Project 3 second.** Adds fan-out, cursors, and backpressure. Same durability backbone.
3. **Project 2 third.** Different science: probabilistic SLOs, eval gates, cost as a reliability signal. Do not dilute it by pretending it is another queue.

If your interviews are ML/platform-heavy, swap 2 and 3. Do not start with 2 if you have never built Project 1’s crash story — you will hand-wave the budget counter.

---

## What approval means

Reply with:

1. Approval or corrections on this plan (especially V1 cuts you want tighter).
2. Which project to open first (default: Project 1).
3. Any constraint I missed (language lock, “must use X,” interview date pressure).

**Next artifact after approval:** `projects/<name>/V1.md` for that project only — state machine, schema sketch, API sketch, drill list mapped to scripts. Still no full codebase until you approve that V1 design.

Do not implement on this gate.
