#!/bin/bash
# Start Matinee. First run creates the Python venv and installs dependencies.
# The server runs under `caffeinate -i` so the Mac does not idle-sleep while serving
# (closing the lid still sleeps it — keep it open or use clamshell mode).
set -e
cd "$(dirname "$0")"
export PYTHONUNBUFFERED=1   # the URL banner shows up immediately, even when logged to a file

if [ ! -x .venv/bin/python ]; then
  echo "Creating virtualenv..."
  python3 -m venv .venv
  .venv/bin/pip install --quiet --upgrade pip
  .venv/bin/pip install --quiet -r requirements.txt
fi

# keep the machine awake while serving where the tool exists (macOS)
if command -v caffeinate >/dev/null 2>&1; then
  exec caffeinate -i .venv/bin/python -m matinee "$@"
else
  exec .venv/bin/python -m matinee "$@"
fi
