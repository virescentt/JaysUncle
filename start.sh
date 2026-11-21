#!/usr/bin/env bash
# Uruchomienie (macOS / Linux / Git Bash na Windows).
#   ./start.sh                 → przetwarza ./input → ./output
#   ./start.sh web             → interfejs webowy http://localhost:8000
#   ./start.sh <komenda> ...   → dowolna komenda CLI (./start.sh --help)
set -euo pipefail
cd "$(dirname "$0")"
if [ -x .venv/bin/python ]; then PY=".venv/bin/python"
elif [ -f .venv/Scripts/python.exe ]; then PY=".venv/Scripts/python.exe"
else PY="python3"; fi
if [ $# -eq 0 ]; then
  exec "$PY" -m odwolania2json run --input input --output output
fi
exec "$PY" -m odwolania2json "$@"
