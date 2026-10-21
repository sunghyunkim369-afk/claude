@echo off
REM coin-quant 매일 실행 (작업 스케줄러가 부름): 데이터 갱신 + 리포트
REM 업비트·바이낸스 일봉은 KST 09:00 에 마감되므로 09:10 이후에 돌리세요.
chcp 65001 > nul
set PYTHONUTF8=1
cd /d "%~dp0\..\.."
if not exist logs mkdir logs
if exist ".venv\Scripts\python.exe" (
  set PY=.venv\Scripts\python.exe
) else (
  set PY=python
)
echo ===== %date% %time% ===== >> logs\daily.log
%PY% -m coinquant daily >> logs\daily.log 2>&1
echo exit code %errorlevel% >> logs\daily.log
