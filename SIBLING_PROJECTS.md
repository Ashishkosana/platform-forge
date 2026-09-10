# Sibling projects (separate repositories)

This remote is the **distributed workflow / job execution engine** only.

| Project | What it is | Git |
| --- | --- | --- |
| 1. Durable step runner | At-least-once linear workflows, Postgres leases, fencing tokens | **This repository** |
| 2. AI reliability control plane | Policy, budgets, traces, eval gates around `complete()` — not a chatbot | **Its own repository** (`ai-reliability-control-plane`) |
| 3. Real-time event / notification platform | Inbox + catch-up + at-least-once webhooks | **Its own repository** (not started) |

Do not copy Project 2 or 3 into this tree. A shared Postgres *server* is fine; shared git history is not.

If you are in the cloud agent workspace and see an `ai-reliability-control-plane/` directory, that directory is a **nested independent git repo**, gitignored here. Push it to a second Origin/GitHub remote. It must never land on this project's `main`.
