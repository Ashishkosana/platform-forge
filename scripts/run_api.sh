#!/usr/bin/env bash
set -euo pipefail
export DEMO_MODE="${DEMO_MODE:-true}"
export DATABASE_URL="${DATABASE_URL:-postgresql://workflow:workflow@127.0.0.1:5432/workflow}"
exec python -m workflow_engine.api
