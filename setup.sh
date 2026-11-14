#!/usr/bin/env bash
# One-time setup: backend venv + deps + browser, and frontend deps.
set -e
here="$(cd "$(dirname "$0")" && pwd)"

echo "==> Backend: creating venv and installing dependencies"
cd "$here/backend"
python3 -m venv .venv
.venv/bin/python -m pip install -U pip
.venv/bin/python -m pip install -r requirements.txt
echo "==> Installing Chromium for Playwright (with system deps where possible)"
.venv/bin/python -m playwright install --with-deps chromium || .venv/bin/python -m playwright install chromium
[ -f .env ] || cp .env.example .env

echo "==> Frontend: installing dependencies"
cd "$here/frontend"
npm install

echo ""
echo "✅ Setup complete. Start everything with:  ./run.sh"
echo "   Then open http://localhost:3000 and add your API key in ⚙ Settings."
