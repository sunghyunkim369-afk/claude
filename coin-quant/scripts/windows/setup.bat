@echo off
REM 처음 한 번: 가상환경 만들고 필요한 패키지 설치, .env 견본 복사
chcp 65001 > nul
cd /d "%~dp0\..\.."
python -m venv .venv || (echo Python 3.11 이상을 먼저 설치하세요 & exit /b 1)
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
if not exist .env copy .env.example .env
echo.
echo 설치 완료. 다음 순서:
echo   1) .venv\Scripts\python.exe -m coinquant update      (처음엔 20~40분)
echo   2) .venv\Scripts\python.exe -m coinquant report
echo   3) powershell -ExecutionPolicy Bypass -File scripts\windows\register_task.ps1
