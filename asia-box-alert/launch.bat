@echo off
cd /d "%~dp0"
if not exist app.py (
  echo ERROR: app.py not found.
  pause
  exit /b 1
)
py -3 updater.py >nul 2>&1
py -3 app.py 2>nul
if %errorlevel%==0 exit /b 0
python updater.py >nul 2>&1
python app.py
if %errorlevel%==0 exit /b 0
echo Python not found. Use run.bat for full install steps.
pause
