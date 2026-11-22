@echo off
setlocal
REM Instalacja srodowiska (Windows): tworzy .venv (Python 3.13+) i instaluje zaleznosci.
REM Uruchamiaj w cmd albo PowerShell (.\setup.bat), NIE w Git Bash.
cd /d "%~dp0"

set "PY="
where py >nul 2>nul
if %errorlevel%==0 (
    py -3.13 -c "import sys" >nul 2>nul && set "PY=py -3.13"
)
if not defined PY (
    py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 13) else 1)" >nul 2>nul && set "PY=py -3"
)
if not defined PY (
    python -c "import sys; sys.exit(0 if sys.version_info >= (3, 13) else 1)" >nul 2>nul && set "PY=python"
)
if not defined PY (
    echo [X] Potrzebny Python 3.13+: https://www.python.org/ftp/python/3.13.17/python-3.13.17-amd64.exe
    echo     Przy instalacji zaznacz "Add python.exe to PATH", potem otworz nowy terminal.
    exit /b 1
)

echo Python: & %PY% --version

REM .venv bez Scripts\python.exe = niepelny (przerwana instalacja) albo z innego systemu (Git Bash, macOS)
if exist .venv if not exist .venv\Scripts\python.exe (
    echo [!] Folder .venv jest niepelny - usuwam i tworze od nowa
    rmdir /s /q .venv
)
if exist .venv if not exist .venv\Scripts\python.exe (
    echo [X] Nie da sie usunac .venv - zamknij VS Code i terminale korzystajace z .venv, potem uruchom ponownie.
    exit /b 1
)
if not exist .venv (
    %PY% -m venv .venv
    if errorlevel 1 (
        echo [X] Nie udalo sie utworzyc .venv
        exit /b 1
    )
)
if not exist .venv\Scripts\python.exe (
    echo [X] Brak .venv\Scripts\python.exe. Uzyj Pythona z python.org, nie z Git Bash/MSYS/Cygwin.
    exit /b 1
)

.venv\Scripts\python.exe -m pip install --upgrade pip -q
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
if errorlevel 1 (
    echo [X] Instalacja zaleznosci nie powiodla sie - sprawdz komunikaty powyzej (np. polaczenie z internetem) i uruchom ponownie.
    exit /b 1
)

if not exist .env copy .env.example .env >nul

where tesseract >nul 2>nul
if not %errorlevel%==0 (
    echo [!] Brak Tesseract ^(OCR dla skanow^). Instalator: https://github.com/UB-Mannheim/tesseract/wiki
    echo     Zaznacz jezyk Polish, albo ustaw TESSERACT_CMD w pliku .env
)

echo [OK] Gotowe. Aktywacja: .venv\Scripts\activate
echo      Dane treningowe:   .venv\Scripts\python scripts\fetch_data.py --sample-input 3
echo      Uruchomienie:      start.bat
