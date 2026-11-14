#!/usr/bin/env bash
# Start backend + frontend reliably: frees stale ports, uses the venv, wires the frontend
# to the backend port automatically. No manual port/path juggling.
set -e
here="$(cd "$(dirname "$0")" && pwd)"

BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-3000}"

# Pick the backend python: prefer the venv, fall back to system python3.
PY="$here/backend/.venv/bin/python"
[ -x "$PY" ] || PY="python3"

free_port() {
  local port="$1"
  if command -v fuser >/dev/null 2>&1; then fuser -k "${port}/tcp" 2>/dev/null || true
  else pkill -f "uvicorn app.main.*${port}" 2>/dev/null || true; fi
}

echo "==> Freeing stale processes on ports ${BACKEND_PORT} and ${FRONTEND_PORT}"
pkill -f "uvicorn app.main" 2>/dev/null || true
pkill -f "next dev" 2>/dev/null || true
free_port "$BACKEND_PORT"
sleep 1

echo "==> Backend on http://localhost:${BACKEND_PORT}  (python: $PY)"
( cd "$here/backend" && "$PY" -m uvicorn app.main:app --port "$BACKEND_PORT" ) &
BACK=$!

# Point the frontend at the chosen backend port.
echo "NEXT_PUBLIC_API_BASE=http://localhost:${BACKEND_PORT}" > "$here/frontend/.env.local"

echo "==> Frontend on http://localhost:${FRONTEND_PORT}"
( cd "$here/frontend" && npm run dev -- -p "$FRONTEND_PORT" ) &
FRONT=$!

trap "kill $BACK $FRONT 2>/dev/null" EXIT INT TERM
echo ""
echo "✅ ReconHawk is starting. Open http://localhost:${FRONTEND_PORT}"
echo "   (Ctrl+C stops both.)"
wait
