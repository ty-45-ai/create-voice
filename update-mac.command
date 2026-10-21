#!/bin/bash
# Update to the newest version from GitHub (stop the app first).
cd "$(dirname "$0")"
git pull && .venv/bin/python -m pip install -r requirements.txt && echo "Updated. Start the app again with start-mac.command"
