@echo off
rem Start the app (double-click). First run sets up the Python environment.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo First run: setting up .venv ...
  python -m venv .venv
  ".venv\Scripts\python" -m pip install --upgrade pip
  ".venv\Scripts\python" -m pip install -r requirements.txt
)
".venv\Scripts\python" -m app
pause
