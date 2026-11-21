@echo off
rem PoE TV: may chu tra gia + CouchSubs ban sua (poe-tv)
taskkill /im CouchSubs.exe >nul 2>&1
timeout /t 3 /nobreak >nul
taskkill /im CouchSubs.exe /f >nul 2>&1
cd /d "%PUBLIC%\poe_tv\poe_tv"
start "PoE TV - may chu tra gia (dung tat)" "%PUBLIC%\poe_tv\venv\Scripts\python.exe" -m poe_tv.serve
timeout /t 5 /nobreak >nul
start "" "%PUBLIC%\poe_tv\couchsubs\CouchSubs.exe"
