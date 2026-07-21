#!/usr/bin/env bash
# Start the whole POC: face-service (backend, :8000) + attendance-ui (frontend, :5173).
# MongoDB must already be running and reachable (see face-service/.env -> MONGO_URI).
# Ctrl+C stops both.
set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"

# ---- 1. Backend: face-service ------------------------------------------------
cd "$ROOT/face-service"
if [ ! -d .venv ]; then
  echo "[setup] creating face-service venv + installing deps (first run, ~few hundred MB)…"
  python3 -m venv .venv
  ./.venv/bin/pip install --upgrade pip
  ./.venv/bin/pip install -r requirements.txt
fi
# load .env (MONGO_URI, MODEL_PACK, ...)
if [ -f .env ]; then set -a; . ./.env; set +a; fi

echo "[run] face-service  -> http://localhost:${PORT:-8000}"
./.venv/bin/uvicorn app.main:app --host "${HOST:-0.0.0.0}" --port "${PORT:-8000}" &
BACKEND_PID=$!
trap 'echo; echo "[stop] shutting down…"; kill $BACKEND_PID 2>/dev/null' EXIT INT TERM

# ---- 2. Frontend: attendance-ui ---------------------------------------------
cd "$ROOT/attendance-ui"
if [ ! -d node_modules ]; then
  echo "[setup] installing attendance-ui deps…"
  npm install
fi
echo "[run] attendance-ui -> http://localhost:5173   (open this one)"
npm run dev
