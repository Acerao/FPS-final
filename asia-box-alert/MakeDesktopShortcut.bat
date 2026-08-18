@echo off
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$dir=(Get-Location).Path; $desk=[Environment]::GetFolderPath('Desktop'); $s=(New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $desk 'AsiaBox.lnk')); $s.TargetPath='wscript.exe'; $s.Arguments='\"'+$dir+'\open.vbs\"'; $s.WorkingDirectory=$dir; $s.WindowStyle=7; $s.Save(); Write-Host 'Desktop shortcut created: AsiaBox.lnk'"
if errorlevel 1 (
  echo Failed to create shortcut.
  pause
  exit /b 1
)
echo Desktop shortcut created: AsiaBox.lnk
pause
