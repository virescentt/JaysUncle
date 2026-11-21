@echo off
REM Instalacja srodowiska (Windows): tworzy .venv (Python 3.13+) i instaluje zaleznosci.
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (
    py -3.13 -c "import sys" >nul 2>nul && set "PY=py -3.13"
)
if not defined PY (
    python -c "import sys; sys.exit(0 if sys.version_info >= (3, 13) else 1)" >nul 2>nul && set "PY=python"
)
if not defined PY (
    echo [X] Potrzebny Python 3.13+  ^(https://www.python.org/downloads/^)
    exit /b 1
)

echo Python: & %PY% --version
if not exist .venv %PY% -m venv .venv
.venv\Scripts\python -m pip install --upgrade pip -q
.venv\Scripts\pip install -r requirements-dev.txt || exit /b 1

if not exist .env copy .env.example .env >nul

where tesseract >nul 2>nul
if not %errorlevel%==0 (
    echo [!] Brak Tesseract ^(OCR dla skanow^). Instalator: https://github.com/UB-Mannheim/tesseract/wiki
    echo     Zaznacz jezyk Polish, albo ustaw TESSERACT_CMD w pliku .env
)

echo [OK] Gotowe. Aktywacja: .venv\Scripts\activate
echo      Dane treningowe:   python scripts\fetch_data.py --sample-input 3
echo      Uruchomienie:      start.bat
