# V2 proposal

**Status:** no V2. V1 benches and drills did not force a design change.

Template for a future change:

```text
Observed evidence:
Measured problem:
Root cause:
Candidate solutions:
Chosen solution:
Rejected alternatives:
Expected benefit:
New complexity:
How we will verify improvement:
```

Closest watch items (not approved work):

1. Sequential submit vs worker count — if a real load generator shows skip-locked wait while CPU is idle.
2. 100 KiB payloads — if production inputs are large, stop putting them on the claim hot path.
3. `LISTEN/NOTIFY` — only if schedule p99 is dominated by poll interval (not seen: p99 ~10 ms with 50–250 ms poll because work was already queued).
