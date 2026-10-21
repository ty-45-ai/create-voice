#!/bin/bash
# Start the app (double-click in Finder). First run sets up the Python environment.
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "First run: setting up .venv ..."
  python3.12 -m venv .venv || python3 -m venv .venv
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/python -m pip install -r requirements.txt
fi
caffeinate -dims .venv/bin/python -m app
