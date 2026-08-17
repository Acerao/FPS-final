@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title 亚盘盒子监测
color 0A
echo ========================================
echo   亚盘盒子监测 启动中
echo   目录: %CD%
echo ========================================
echo.

if not exist "app.py" (
  echo [错误] 当前文件夹里没有 app.py
  echo 请解压后进入 asia-box-alert 这个目录，再双击 run.bat
  echo.
  pause
  exit /b 1
)

set "PY=py"
set "PYARGS=-3"
py -3 --version >nul 2>&1
if errorlevel 1 (
  set "PY=python"
  set "PYARGS="
  python --version >nul 2>&1
  if errorlevel 1 (
    echo [错误] 没有找到 Python。
    echo.
    echo 请打开 https://www.python.org/downloads/ 安装 3.10 或更新版本
    echo 安装时务必勾选: Add python.exe to PATH
    echo 不要用微软商店那个 Python 快捷方式。
    echo.
    pause
    exit /b 1
  )
)

echo 使用解释器:
%PY% %PYARGS% --version
%PY% %PYARGS% -c "import sys; print(sys.executable)"
echo.

%PY% %PYARGS% -c "import sys; raise SystemExit(1 if 'WindowsApps' in sys.executable.replace('\\','/') else 0)"
if errorlevel 1 (
  echo [错误] 现在用的是微软商店的 Python 空壳，不能运行本程序。
  echo 请到 python.org 安装正式版，并勾选 Add python.exe to PATH。
  echo.
  pause
  exit /b 1
)

echo [1/3] 安装依赖 requests ...
%PY% %PYARGS% -m pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo [错误] pip 安装失败。把上面红字截图发我。
  echo.
  pause
  exit /b 1
)

echo [2/3] 检查 tkinter / requests ...
%PY% %PYARGS% -c "import tkinter,requests; print('依赖正常')"
if errorlevel 1 (
  echo.
  echo [错误] 缺 tkinter 或 requests。
  echo 请卸载后重装 Python，安装界面点 Customize，勾选 tcl/tk 和 pip。
  echo.
  pause
  exit /b 1
)

echo [3/3] 打开监测窗口 ...
echo 若一闪而过，请看同目录 error.log
echo.
%PY% %PYARGS% app.py
set "ERR=%ERRORLEVEL%"
echo.
if not "%ERR%"=="0" (
  echo 程序异常退出，代码 %ERR%
  if exist error.log (
    echo ---- error.log ----
    type error.log
  )
)
echo.
pause
