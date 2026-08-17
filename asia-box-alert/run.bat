@echo off
cd /d "%~dp0"
title AsiaBox
echo Starting Asia Box monitor...
echo Folder: %CD%
echo.

if not exist app.py (
  echo ERROR: app.py not found in this folder.
  pause
  exit /b 1
)

py -3 --version >nul 2>&1
if %errorlevel%==0 goto USEPY
python --version >nul 2>&1
if %errorlevel%==0 goto USEPYTHON
echo ERROR: Python not found. Install Python 3.10+ from python.org
echo and check "Add python.exe to PATH".
pause
exit /b 1

:USEPY
echo Using: py -3
py -3 --version
echo Installing packages...
py -3 -m pip install -r requirements.txt
if errorlevel 1 goto PIPFAIL
echo Launching GUI...
py -3 app.py
goto END

:USEPYTHON
echo Using: python
python --version
echo Installing packages...
python -m pip install -r requirements.txt
if errorlevel 1 goto PIPFAIL
echo Launching GUI...
python app.py
goto END

:PIPFAIL
echo ERROR: pip install failed.
pause
exit /b 1

:END
echo.
echo Program ended.
pause
