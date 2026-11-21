#!/usr/bin/env bash
# Run the dashboard locally: API on :8000, Vite dev server on :5173. Ctrl-C stops both.
set -euo pipefail
cd "$(dirname "$0")/.."

[ -d web/node_modules ] || (cd web && npm install)

trap 'kill 0' INT TERM EXIT
uv run uvicorn ask_soony.api:app --reload --port 8000 &
(cd web && npm run dev) &
wait
