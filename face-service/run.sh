#!/usr/bin/env bash
# Set up (first run) and start the face-service on http://localhost:8000
set -e
cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
  echo "Creating virtualenv + installing deps (first run, downloads a few hundred MB)…"
  python3 -m venv .venv
  ./.venv/bin/pip install --upgrade pip
  ./.venv/bin/pip install -r requirements.txt
fi

# Load .env if present
# shellcheck disable=SC1091
if [ -f .env ]; then
  set -a
  . ./.env
  set +a
fi

echo "Starting Rean face-service → http://localhost:${PORT:-8000}"
echo "(First request downloads the '${MODEL_PACK:-buffalo_l}' model to ~/.insightface — one-time.)"
exec ./.venv/bin/uvicorn app.main:app --host "${HOST:-0.0.0.0}" --port "${PORT:-8000}"
