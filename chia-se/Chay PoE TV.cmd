@echo off
rem PoE TV: may chu tra gia + CouchSubs ban sua (poe-tv)
taskkill /im CouchSubs.exe >nul 2>&1
timeout /t 3 /nobreak >nul
taskkill /im CouchSubs.exe /f >nul 2>&1
cd /d "%~dp0poe_tv"
start "PoE TV - may chu tra gia (dung tat)" "%~dp0venv\Scripts\python.exe" -m poe_tv.serve
timeout /t 5 /nobreak >nul
start "" "%~dp0couchsubs\CouchSubs.exe"
