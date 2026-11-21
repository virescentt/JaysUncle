#!/usr/bin/env bash
# Instalacja środowiska (macOS / Linux): tworzy .venv (Python 3.13+) i instaluje zależności.
set -euo pipefail
cd "$(dirname "$0")"

PY=""
for cand in python3.13 python3.14 python3; do
  if command -v "$cand" >/dev/null 2>&1; then
    if "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 13) else 1)'; then
      PY="$cand"; break
    fi
  fi
done
if [ -z "$PY" ]; then
  echo "❌ Potrzebny Python 3.13+. macOS: brew install python@3.13 | Linux: pyenv / deadsnakes"
  exit 1
fi

echo "▶ Python: $($PY --version)"
[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/python -m pip install --upgrade pip -q
.venv/bin/pip install -r requirements-dev.txt

[ -f .env ] || cp .env.example .env

if ! command -v tesseract >/dev/null 2>&1; then
  echo "⚠ Brak Tesseract (OCR dla skanów). Zainstaluj:"
  echo "    macOS:  brew install tesseract tesseract-lang"
  echo "    Ubuntu: sudo apt install tesseract-ocr tesseract-ocr-pol"
  echo "  Do tego czasu działa zapasowy RapidOCR (gorzej z polskimi znakami)."
fi

echo "✅ Gotowe. Aktywacja: source .venv/bin/activate"
echo "   Dane treningowe:   python scripts/fetch_data.py --sample-input 3"
echo "   Uruchomienie:      ./start.sh"
