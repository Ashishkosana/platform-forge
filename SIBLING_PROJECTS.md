# Three repositories — not a monorepo

Each system is a **separate git remote**. Same Postgres *server* is allowed. Shared git history is not.

| # | System | GitHub | Status |
| --- | --- | --- | --- |
| 1 | Durable workflow / job execution engine | https://github.com/Ashishkosana/platform-forge | V1 + V2 (`LISTEN` wake) |
| 2 | AI reliability control plane | https://github.com/Ashishkosana/ai-reliability-control-plane | V1 + V2 (async traces) |
| 3 | Real-time event / notification platform | https://github.com/Ashishkosana/realtime-event-platform | V1 + V2 (chunked fan-out) |

All three `main` branches are on GitHub (private). Origin remotes exist under `ashishkosanagmailcom/` for the same names; push those from a machine whose Origin token is scoped for them:

```bash
git remote add origin https://origin.cursor.com/ashishkosanagmailcom/<name>.git
git push -u origin main
```
