# V2 proposal

**Status:** V2 implemented: `LISTEN/NOTIFY` worker wake.

Observed evidence: V1 workers slept `POLL_INTERVAL_SECONDS` (default 250 ms) when idle. Benches with a pre-queued backlog did not show that wait; idle submit-to-start did.

Measured problem: idle schedule latency is dominated by poll interval, not skip-locked.

Chosen solution: `NOTIFY workflow_wake` on submit, successful complete (next step), and replay. Workers `LISTEN` with the poll interval as a **timeout fallback** so a missed notify cannot stall the fleet. Claim path is unchanged (`SKIP LOCKED` + fencing). `WAKE_MODE=poll` restores V1.

Rejected: Redis pubsub, Kafka, shrinking poll to 5 ms in production (busy-wait).

How we verify: existing crash drills still pass; idle workers return to claim without waiting a full 250 ms after submit.
