@echo off
rem Start the app (double-click). First run sets up the Python environment.
cd /d "%~dp0"
if not exist ".env" copy ".env.example" ".env" >nul
if not exist ".venv\Scripts\python.exe" (
  echo First run: setting up .venv ...
  python -m venv .venv
  ".venv\Scripts\python" -m pip install --upgrade pip
  ".venv\Scripts\python" -m pip install -r requirements.txt
)
start "" cmd /c "timeout /t 4 >nul & start http://127.0.0.1:8000/admin"
".venv\Scripts\python" -m app
pause
