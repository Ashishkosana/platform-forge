# Three repositories — not a monorepo

Each system is a **separate git remote**. Same Postgres *server* is allowed. Shared git history is not. Do not add Project 2 or Project 3 as folders, packages, or submodules here.

| # | System | Repository name | Status |
| --- | --- | --- | --- |
| 1 | Durable workflow / job execution engine | **this repository** | V1 implemented |
| 2 | AI reliability control plane (not a chatbot) | `ai-reliability-control-plane` | V1 implemented in its **own** git; needs its own Origin remote |
| 3 | Real-time event and notification platform | `realtime-event-platform` | Not started; will be a **third** remote |

## What this tree must never contain

- `ai-reliability-control-plane/`
- `realtime-event-platform/`
- A `projects/` directory of multiple codebases

Planning docs for all three live under `docs/planning/` because the plan was written before the remotes existed. That is documentation, not a license to implement Project 2 or 3 in this repo.

## Create the other remotes

This agent cannot create Origin repositories with the current token. In Cursor, create two **empty** repos (Create repo), then:

```bash
# Project 2 — already committed locally outside this worktree
cd /path/to/ai-reliability-control-plane
git remote add origin <origin-url-for-ai-reliability-control-plane>
git push -u origin main

# Project 3 — empty repo only, no code until that project's V1 design is the active gate
# create remote: realtime-event-platform
```

After those remotes exist, send the URLs and the control-plane history can be pushed, and Project 3 can start in a new agent on that repo.
