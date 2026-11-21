#!/usr/bin/env bash
# Instalacja środowiska (macOS / Linux / Git Bash na Windows): tworzy .venv (Python 3.13+) i instaluje zależności.
set -euo pipefail
cd "$(dirname "$0")"

ok_version() { $1 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 13) else 1)' >/dev/null 2>&1; }

PY=""
for cand in python3.13 python3.14 python3 python "py -3.13" "py -3"; do
  if command -v "${cand%% *}" >/dev/null 2>&1 && ok_version "$cand"; then
    PY="$cand"; break
  fi
done
if [ -z "$PY" ]; then
  echo "❌ Potrzebny Python 3.13+. macOS: brew install python@3.13 | Linux: pyenv / deadsnakes"
  echo "   Windows: https://www.python.org/ftp/python/3.13.17/python-3.13.17-amd64.exe"
  exit 1
fi

# Python w .venv: bin/ (macOS/Linux) albo Scripts/ (Windows)
venv_python() {
  if [ -x .venv/bin/python ]; then echo .venv/bin/python
  elif [ -f .venv/Scripts/python.exe ]; then echo .venv/Scripts/python.exe
  fi
}

echo "▶ Python: $($PY --version)"
if [ -d .venv ] && [ -z "$(venv_python)" ]; then
  echo "⚠ Folder .venv jest niepełny — usuwam i tworzę od nowa"
  rm -rf .venv
fi
[ -d .venv ] || $PY -m venv .venv
VPY="$(venv_python)"
if [ -z "$VPY" ]; then
  echo "❌ Nie udało się utworzyć .venv (brak .venv/bin/python i .venv/Scripts/python.exe)"
  exit 1
fi

"$VPY" -m pip install --upgrade pip -q
"$VPY" -m pip install -r requirements-dev.txt

[ -f .env ] || cp .env.example .env

if ! command -v tesseract >/dev/null 2>&1; then
  echo "⚠ Brak Tesseract (OCR dla skanów). Zainstaluj:"
  echo "    macOS:  brew install tesseract tesseract-lang"
  echo "    Ubuntu: sudo apt install tesseract-ocr tesseract-ocr-pol"
  echo "  Do tego czasu działa zapasowy RapidOCR (gorzej z polskimi znakami)."
fi

echo "✅ Gotowe. Aktywacja: source .venv/bin/activate   (Git Bash na Windows: source .venv/Scripts/activate)"
echo "   Dane treningowe:   python scripts/fetch_data.py --sample-input 3"
echo "   Uruchomienie:      ./start.sh"
