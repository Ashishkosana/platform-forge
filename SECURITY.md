# Security review (lightweight)

Performed against this V1 tree. No enterprise auth was in scope.

## Findings and mitigations

| Issue | Status |
| --- | --- |
| SQL injection | Parameterized psycopg (`%s` / `%(name)s`). No string-built SQL with user values except `CREATE DATABASE` in tests using a parsed DB name from DSN. |
| Secrets in git | `.env` gitignored. `.env.example` has a local password `workflow` — acceptable for a lab, **not** for production. |
| SSRF via `http_post` | Host allowlist `ALLOWED_HTTP_HOSTS` (default localhost). Non-http(s) rejected. |
| Unbounded body | `MAX_BODY_BYTES` middleware (default 1_000_000) + Pydantic field lengths. |
| Unbounded retries | `max_attempts` per step; jittered backoff; dead-letter. |
| Dangerous logging | JSON logs include ids and errors, not raw job payloads on the worker event line. Job input is still in Postgres. |
| PII | Lab engine. Do not put real PII in `input`. No retention policy beyond “it’s in `jobs.input` JSONB.” |
| Command injection | Demo worker spawn uses `sys.executable` + module name, not shell. |
| Demo kill/stop | Only if `DEMO_MODE=true`. Must be false on any shared network. |
| Auth | **None.** Production needs a reverse proxy, network policy, or mTLS. |
| Resource exhaustion | Polling workers + skip-locked is CPU+DB. No per-tenant quota. Public deploy would need rate limits. |
| Deserialization | JSON via Pydantic / jsonb. No pickle. |

## Production auth (not implemented)

- Disable demo routes.
- Authenticate submit/cancel/replay (service identity).
- Do not expose Postgres.
- Rotate the `workflow` password; never use SUPERUSER for the app role (tests currently use a superuser for `CREATE DATABASE`).
