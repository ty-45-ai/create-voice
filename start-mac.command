#!/bin/bash
# Start the app (double-click in Finder). First run sets up the Python environment.
cd "$(dirname "$0")"
if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env (settings file)."
fi
if [ ! -x .venv/bin/python ]; then
  echo "First run: setting up .venv ..."
  python3.13 -m venv .venv || python3 -m venv .venv || exit 1
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/python -m pip install -r requirements.txt || exit 1
fi
(sleep 4; open "http://127.0.0.1:8000/admin") &
caffeinate -dims .venv/bin/python -m app
