#!/usr/bin/env bash
set -euo pipefail
export DATABASE_URL="${DATABASE_URL:-postgresql://workflow:workflow@127.0.0.1:5432/workflow}"
export WORKER_CONCURRENCY="${WORKER_CONCURRENCY:-1}"
exec python -m workflow_engine.worker
