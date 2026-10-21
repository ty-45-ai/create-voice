@echo off
rem Update to the newest version from GitHub (stop the app first).
cd /d "%~dp0"
git pull
if errorlevel 1 (
  echo.
  echo Update failed. Is Git installed and is this folder a git clone?
  pause
  exit /b 1
)
".venv\Scripts\python" -m pip install -r requirements.txt
echo.
echo Updated. Start the app again with start-windows.bat
pause
