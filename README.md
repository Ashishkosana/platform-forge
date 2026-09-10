# Portfolio systems lab

Three systems, built as laboratories — not as a code dump.

| Project | One-line problem |
| --- | --- |
| Distributed workflow / job execution engine | Durable multi-step work with leases, retries, and honest delivery semantics |
| AI reliability control plane | Policy, budgets, traces, and eval gates around model calls — not a chatbot |
| Real-time event and notification platform | Ingest once, fan-out, catch-up, and at-least-once channel delivery |

This repo is in the **Project 1 V1 design gate**. There is no application to run yet.

## Read these, in order

1. [MASTER_MACRO_PROMPT.md](MASTER_MACRO_PROMPT.md) — standing contract for how we work
2. [THREE_PROJECT_PLAN.md](THREE_PROJECT_PLAN.md) — three-project plan
3. [projects/workflow-engine/V1.md](projects/workflow-engine/V1.md) — Project 1 V1 design (Python + Postgres)

## What happens next

Nothing is implemented until `projects/workflow-engine/V1.md` is approved.

Then: V1 code + benchmarks + failure drills for Project 1 only → V2 only where measurements force it.
