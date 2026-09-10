# Three repositories — not a monorepo

Each system is a **separate git remote**. Same Postgres *server* is allowed. Shared git history is not.

| # | System | Repository name | Status |
| --- | --- | --- | --- |
| 1 | Durable workflow / job execution engine | **this repository** | V1 + V2 (`LISTEN` wake) |
| 2 | AI reliability control plane | `ai-reliability-control-plane` | V1 + V2 (async traces); own git |
| 3 | Real-time event / notification platform | `realtime-event-platform` | V1 + V2 (chunked fan-out); own git |

Push each repo to **both** Origin and GitHub when remotes exist:

```bash
git remote add origin <origin-url>
git remote add github git@github.com:<org>/<name>.git
git push -u origin main
git push -u github main
```

This agent’s Origin token cannot `origin repo create`. `gh` is not logged in here, so GitHub.com creates/pushes must be done after `gh auth login` (or by creating the empty repos in the GitHub UI and sending the URLs).
