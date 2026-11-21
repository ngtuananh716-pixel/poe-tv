@echo off
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tep\cap-nhat.ps1"
echo.
pause
