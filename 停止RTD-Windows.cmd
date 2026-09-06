@echo off
setlocal
chcp 65001 >nul
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0停止RTD-Windows.ps1"
if errorlevel 1 (
  echo.
  echo RTD 停止未完成，请查看上方提示。
  pause
)
endlocal
