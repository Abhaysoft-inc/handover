#!/usr/bin/env bash
# Handover: one-command start for macOS / Linux.
#   ./start.sh         build what's missing, then serve on http://localhost:8000
#   ./start.sh --dev   API with auto-reload + Vite dev server on http://localhost:5173
set -euo pipefail
cd "$(dirname "$0")"

command -v uv >/dev/null || { echo "Handover needs uv: https://docs.astral.sh/uv/getting-started/installation/"; exit 1; }
command -v npm >/dev/null || { echo "Handover needs Node.js 20+: https://nodejs.org"; exit 1; }
[ -f .env ] || { cp .env.example .env; echo "Created .env from .env.example"; }

uv sync
[ -d frontend/node_modules ] || (cd frontend && npm install)
[ -d sample_data/demo_drive ] || uv run python scripts/generate_corpus.py

if [ "${1:-}" = "--dev" ]; then
  (cd frontend && npm run dev) &
  trap 'kill %1 2>/dev/null' EXIT
  echo -e "\nHandover (dev): dashboard http://localhost:5173 · API http://localhost:8000\n"
  uv run uvicorn handover.main:app --port 8000 --reload --reload-dir handover
else
  [ -f frontend/dist/index.html ] || (cd frontend && npm run build)
  echo -e "\nHandover is running at http://localhost:8000\n"
  uv run uvicorn handover.main:app --port 8000
fi
