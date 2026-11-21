@echo off
REM Uruchomienie (Windows).
REM   start.bat               -> przetwarza .\input -> .\output
REM   start.bat web           -> interfejs webowy http://localhost:8000
REM   start.bat <komenda> ... -> dowolna komenda CLI (start.bat --help)
cd /d "%~dp0"
set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
if "%~1"=="" (
    "%PY%" -m odwolania2json run --input input --output output
) else (
    "%PY%" -m odwolania2json %*
)
