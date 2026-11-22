@echo off
REM Uruchomienie (Windows). Przy pierwszym uruchomieniu automatycznie instaluje srodowisko (setup.bat).
REM   start.bat               -> przetwarza .\input -> .\output
REM   start.bat web           -> interfejs webowy http://localhost:8000
REM   start.bat <komenda> ... -> dowolna komenda CLI (start.bat --help)
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo [i] Pierwsze uruchomienie: instalacja srodowiska ^(kilka minut^)...
    call setup.bat
    if errorlevel 1 exit /b 1
)
set "PY=.venv\Scripts\python.exe"
if "%~1"=="" (
    "%PY%" -m odwolania2json run --input input --output output
) else (
    "%PY%" -m odwolania2json %*
)
