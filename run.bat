@echo off
rem Matinee - creates the Python environment on first run, then starts the server.
rem Double-click this file, or run it from a terminal.
cd /d "%~dp0"
set PYTHONUNBUFFERED=1
if not exist .venv\Scripts\python.exe (
  echo Creating Python environment (first run only)...
  py -3 -m venv .venv 2>nul || python -m venv .venv
  if not exist .venv\Scripts\python.exe (
    echo Python 3.9+ is required - install it from https://www.python.org/downloads/
    echo and tick "Add python.exe to PATH" in the installer.
    pause
    exit /b 1
  )
  .venv\Scripts\python -m pip install --quiet --upgrade pip
  .venv\Scripts\python -m pip install --quiet -r requirements.txt
)
.venv\Scripts\python -m matinee %*
pause
