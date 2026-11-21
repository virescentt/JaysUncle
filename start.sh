#!/usr/bin/env bash
# Uruchomienie (macOS / Linux).
#   ./start.sh                 → przetwarza ./input → ./output
#   ./start.sh web             → interfejs webowy http://localhost:8000
#   ./start.sh <komenda> ...   → dowolna komenda CLI (./start.sh --help)
set -euo pipefail
cd "$(dirname "$0")"
PY=".venv/bin/python"; [ -x "$PY" ] || PY="python3"
if [ $# -eq 0 ]; then
  exec "$PY" -m odwolania2json run --input input --output output
fi
exec "$PY" -m odwolania2json "$@"
