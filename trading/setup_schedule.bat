@echo off
rem Double-click: register Windows scheduled tasks for contest mode (wakes PC from sleep)
chcp 65001 >nul
cd /d "%~dp0.."
python -m trading update
python -m trading schedule
pause
