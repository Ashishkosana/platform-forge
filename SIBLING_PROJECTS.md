# Three repositories — not a monorepo

Each system is a **separate git remote**. Same Postgres *server* is allowed. Shared git history is not.

| # | System | Repository name | Status |
| --- | --- | --- | --- |
| 1 | Durable workflow / job execution engine | **this repository** | V1 + V2 (`LISTEN` wake) |
| 2 | AI reliability control plane | `ai-reliability-control-plane` | V1 + V2 (async traces); own git |
| 3 | Real-time event / notification platform | `realtime-event-platform` | V1 + V2 (chunked fan-out); own git |

Push each repo to **both** Origin and GitHub. The agent VM cannot do that: its Origin token is scoped to this session only, and `gh` is not logged in here.

GitHub empty remotes (your WSL session):

- https://github.com/Ashishkosana/ai-reliability-control-plane
- https://github.com/Ashishkosana/realtime-event-platform
- https://github.com/Ashishkosana/platform-forge — create if missing

Origin remotes already exist under `ashishkosanagmailcom/` for all three names.

**On WSL**, after `gh auth login`, copy `handoff/` from this tree and run `./handoff/push_to_remotes.sh`. That clones the `.bundle` files (existing history) and pushes `main` to `github` and `origin`. Delete `handoff/` from this repo after a successful push.
