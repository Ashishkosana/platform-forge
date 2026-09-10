# Failure drills

All drills except Postgres bounce run in `pytest` against a real PostgreSQL and real worker **subprocesses**. They do not mock `CLAIM_SQL`.

Default test lease TTL is **2s**, heartbeat **0.4s**, poll **0.05s**.

| Drill | Test | Result in this repo |
| --- | --- | --- |
| Crash after naive effect | `drills/test_failure_drills.py::test_crash_after_naive_duplicates_effect` | Pass. Attempt ≥ 2. `lab_effects.write_count >= 2`. |
| Crash after idempotent effect | `test_crash_after_idempotent_effect_stays_one` | Pass. Attempt ≥ 2. `write_count == 1`. |
| Zombie + fence | `test_zombie_fence_reject` | Pass. `SIGSTOP` holder, second worker steals, `SIGCONT` cannot succeed twice. |
| Poison vs healthy | `test_poison_does_not_block_healthy_work` | Pass. `always_fail` dead-letters; `noop` succeeds. |
| Retry herd / jitter | `test_retry_jitter_spreads` | Pass. 40 jobs (override `DRILL_HERD_SIZE`) reach dead-letter; 3 attempts on `herd-0`. |
| Cancel during hang | `test_cancel_during_hang` | Pass. Job `cancelled`; step not `succeeded`. |
| Duplicate submit | `tests/test_api.py::test_concurrent_duplicate_submit` | Pass. 50 parallel POSTs → one job row. |
| Clock / SQL `now()` | `tests/test_backoff_and_sql.py`, `test_clock_skew_sql_uses_now` | Pass. Claim/heartbeat SQL contain `now()`. |
| Postgres bounce | `test_postgres_bounce` | **SKIPPED** unless `DRILL_PG_BOUNCE=1`. Stopping Postgres on a shared instance is unsafe in CI. **NOT MEASURED** here. |

## Crash-handler lab concession

`crash_naive` / `crash_idempotent` call `os._exit(1)` only when `attempt_count <= 1`. A handler that always dies after the write never completes. That is documented in `handlers.py` and ARCHITECTURE.md. The engine’s recovery path is still the real one: expired lease, steal, new fencing token.

## How to run

```bash
pytest drills/ -q
pytest -q                    # includes tests/ + drills/
DRILL_HERD_SIZE=200 pytest drills/test_failure_drills.py::test_retry_jitter_spreads
```

## Manual console sequence (`DEMO_MODE=true`)

1. Start API and click **Start worker**.
2. Submit `linear_demo`. Watch steps go pending → running → succeeded.
3. Submit `crash_naive`. SIGKILL the worker (or let it exit). Start another worker. Inspect `lab_effects.write_count`.
4. Submit `hang` with `{"seconds": 30}`. SIGSTOP the worker. Wait past lease TTL. Start a second worker. SIGCONT the first. Only one `succeeded` attempt.
5. Submit `always_fail`. Wait for dead-letter. Replay.
