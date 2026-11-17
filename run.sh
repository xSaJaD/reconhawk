#!/usr/bin/env bash
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-3000}"
PY="$here/backend/.venv/bin/python"
[ -x "$PY" ] || { echo 'Run ./setup.sh first'; exit 1; }
# Retain configured origins and allow the local UI on the selected port.
if [ -z "${CORS_ORIGINS:-}" ]; then
 CORS_ORIGINS="$(cd "$here/backend"; "$PY" -c 'from app.config import settings; print(settings.cors_origins)')"
fi
export CORS_ORIGINS="$CORS_ORIGINS,http://localhost:$FRONTEND_PORT,http://127.0.0.1:$FRONTEND_PORT"
"$PY" - "$BACKEND_PORT" "$FRONTEND_PORT" <<'PY'
import socket,sys
for port in map(int,sys.argv[1:]):
    with socket.socket() as sock:
        try: sock.bind(('0.0.0.0',port))
        except OSError: raise SystemExit(f'Port {port} is busy; change the port. No processes were killed.')
PY
cleanup() {
 trap - EXIT INT TERM
 [ -z "${BACK:-}" ] || kill -- "-$BACK" 2>/dev/null || true
 [ -z "${FRONT:-}" ] || kill -- "-$FRONT" 2>/dev/null || true
}
trap cleanup EXIT INT TERM
(cd "$here/backend"; exec setsid "$PY" -m uvicorn app.main:app --host "${BACKEND_HOST:-127.0.0.1}" --port "$BACKEND_PORT") &
BACK=$!
(cd "$here/frontend"; export NEXT_PUBLIC_API_BASE="${NEXT_PUBLIC_API_BASE:-http://localhost:$BACKEND_PORT}"; exec setsid npm run dev -- --port "$FRONTEND_PORT") &
FRONT=$!
echo "ReconHawk: http://localhost:$FRONTEND_PORT"
wait -n "$BACK" "$FRONT"
