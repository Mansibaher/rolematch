#!/usr/bin/env bash
# Local dev: API on :8000 and built frontend on :3000 (no Docker).
set -e
ROOT=$(cd "$(dirname "$0")" && pwd)
(cd "$ROOT/backend" && . .venv/bin/activate && uvicorn app.main:app --host 127.0.0.1 --port 8000) &
(cd "$ROOT/frontend" && NEXT_TELEMETRY_DISABLED=1 npx next start -p 3000) &
wait
