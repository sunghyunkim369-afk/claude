@echo off
rem 더블클릭으로 대회 모드 시작: 위치와 상관없이 저장소 폴더로 이동해서 최신 버전을 받고 실행해요
chcp 65001 >nul
cd /d "%~dp0.."
python -m trading update
python -m trading contest
pause
