#!/usr/bin/env bash
# Serve the POC over http://localhost:8000 — localhost counts as a secure
# context, so the browser will allow live camera access.
cd "$(dirname "$0")"
PORT="${1:-8000}"
echo "Rean Face POC  →  http://localhost:${PORT}"
echo "Open that URL in Chrome/Safari. Ctrl+C to stop."
if command -v python3 >/dev/null 2>&1; then
  python3 -m http.server "$PORT"
elif command -v npx >/dev/null 2>&1; then
  npx --yes serve -l "$PORT" .
else
  echo "Need python3 or node/npx installed."; exit 1
fi
