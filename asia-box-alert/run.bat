@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title 亚盘盒子监测
color 0A
echo ========================================
echo   亚盘盒子监测
echo   %CD%
echo ========================================
echo.

if not exist "%~dp0app.py" (
  echo [错误] 找不到 app.py
  echo 请进入 asia-box-alert 文件夹后再运行。
  pause
  exit /b 1
)

where py >nul 2>&1
if %errorlevel%==0 (
  echo 使用: py -3
  py -3 --version
  echo.
  echo 正在安装依赖...
  py -3 -m pip install -r "%~dp0requirements.txt"
  if errorlevel 1 goto PIPFAIL
  echo.
  echo 正在启动窗口...
  py -3 "%~dp0app.py"
  goto END
)

where python >nul 2>&1
if %errorlevel%==0 (
  echo 使用: python
  python --version
  echo.
  echo 正在安装依赖...
  python -m pip install -r "%~dp0requirements.txt"
  if errorlevel 1 goto PIPFAIL
  echo.
  echo 正在启动窗口...
  python "%~dp0app.py"
  goto END
)

echo [错误] 没有找到 Python。
echo 请安装 https://www.python.org/downloads/ 并勾选 Add python.exe to PATH
pause
exit /b 1

:PIPFAIL
echo.
echo [错误] pip 安装失败，把上面文字截图发我。
pause
exit /b 1

:END
echo.
echo 程序已结束。
pause
