#!/usr/bin/env bash
# One-command setup and launch: venv, deps, fixtures, backup case, web build, server on :8000.
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  if command -v uv >/dev/null; then uv venv -q -p 3.12 .venv; else python3 -m venv .venv; fi
fi
if ! .venv/bin/python -c "import fastapi, cyber_arche" 2>/dev/null; then
  if command -v uv >/dev/null; then VIRTUAL_ENV=.venv uv pip install -q -e ".[dev]"; else .venv/bin/pip install -q -e ".[dev]"; fi
fi
[ -d fixtures/out ] || .venv/bin/python fixtures/make_fixtures.py
[ -f fixtures/cases/backup/messages.db ] || .venv/bin/python fixtures/make_backup.py
if [ ! -f web/dist/index.html ]; then (cd web && npm install --silent && npm run build); fi
[ -f .env ] && set -a && . ./.env && set +a
PORT="${PORT:-8000}"
if lsof -ti "tcp:$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Cyber Arche (or something else) is already running on port $PORT."
  echo "Open http://localhost:$PORT, or stop it first:  kill \$(lsof -ti tcp:$PORT -sTCP:LISTEN)"
  exit 1
fi
HOST="${HOST:-127.0.0.1}"
if [ "$HOST" = "0.0.0.0" ]; then
  IP="$(ipconfig getifaddr en0 2>/dev/null || hostname -I 2>/dev/null | awk '{print $1}')"
  echo "Phone mode: on the same Wi-Fi, open  http://${IP:-YOUR-LAPTOP-IP}:$PORT/demo"
fi
exec .venv/bin/uvicorn api.main:app --host "$HOST" --port "$PORT"
