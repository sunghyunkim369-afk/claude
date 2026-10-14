@echo off
rem 더블클릭으로 잔고 보기
chcp 65001 >nul
cd /d "%~dp0.."
python -m trading status
pause
