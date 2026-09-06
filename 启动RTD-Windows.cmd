@echo off
setlocal
chcp 65001 >nul
start "" /b wscript.exe "%~dp0start-rtd-windows.vbs" "%~dp0启动RTD-Windows.ps1"
endlocal
