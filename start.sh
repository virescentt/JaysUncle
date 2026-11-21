#!/usr/bin/env bash
# Uruchomienie (macOS / Linux / Git Bash na Windows).
# Przy pierwszym uruchomieniu automatycznie instaluje środowisko (setup.sh).
#   ./start.sh                 → przetwarza ./input → ./output
#   ./start.sh web             → interfejs webowy http://localhost:8000
#   ./start.sh <komenda> ...   → dowolna komenda CLI (./start.sh --help)
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ] && [ ! -f .venv/Scripts/python.exe ]; then
  echo "ℹ Pierwsze uruchomienie: instalacja środowiska (kilka minut)..."
  ./setup.sh
fi
if [ -x .venv/bin/python ]; then PY=".venv/bin/python"
else PY=".venv/Scripts/python.exe"; fi
if [ $# -eq 0 ]; then
  exec "$PY" -m odwolania2json run --input input --output output
fi
exec "$PY" -m odwolania2json "$@"
