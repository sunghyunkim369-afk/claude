"""Windows 작업 스케줄러에 대회 모드를 예약해요. PC 가 절전 상태여도 깨워서 실행해요 (완전히 꺼져 있으면 안 돼요).

  python -m trading schedule     예약 등록 (평일 09:10 국내, 평일 23:40 미국)
  python -m trading unschedule   예약 지우기

23:40 은 미국 정규장(서머타임 22:30~05:00, 그 밖 23:30~06:00) 안이라 11월 서머타임이 끝나도 그대로 써요.
"""

import os
import subprocess
import sys
import tempfile
from datetime import date
from xml.sax.saxutils import escape

TASKS = {
    "KIS 대회 국내": "09:10:00",
    "KIS 대회 미국": "23:40:00",
}


def task_xml(python, workdir, start_time, today=None):
    today = today or date.today()
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>BanKIS 모의투자대회 자동매매 (python -m trading contest --once)</Description></RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>{today.isoformat()}T{start_time}</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByWeek>
        <DaysOfWeek><Monday /><Tuesday /><Wednesday /><Thursday /><Friday /></DaysOfWeek>
        <WeeksInterval>1</WeeksInterval>
      </ScheduleByWeek>
    </CalendarTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author"><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <WakeToRun>true</WakeToRun>
    <ExecutionTimeLimit>PT1H</ExecutionTimeLimit>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{escape(python)}</Command>
      <Arguments>-m trading contest --once</Arguments>
      <WorkingDirectory>{escape(workdir)}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def register(here, run=subprocess.run, out=print, us=True):
    """us=False 면 (미국을 꺼 뒀으면) 미국 예약은 안 걸고, 이미 있으면 지워요."""
    if os.name != "nt":
        sys.exit("작업 스케줄러 예약은 Windows 에서만 돼요.")
    workdir = os.path.dirname(here)
    python = sys.executable.replace("python.exe", "pythonw.exe") if sys.executable.endswith("python.exe") else sys.executable
    if not os.path.exists(python):
        python = sys.executable
    for name, at in TASKS.items():
        if not us and name == "KIS 대회 미국":
            r = run(["schtasks", "/Delete", "/TN", name, "/F"], capture_output=True, text=True, errors="replace")
            out(f"미국은 꺼 둬서 예약 안 함{' (있던 예약 지움)' if r.returncode == 0 else ''}: {name}")
            continue
        with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False, encoding="utf-16") as f:
            f.write(task_xml(python, workdir, at))
            path = f.name
        try:
            r = run(["schtasks", "/Create", "/TN", name, "/XML", path, "/F"], capture_output=True, text=True, errors="replace")
        finally:
            os.unlink(path)
        if r.returncode != 0:
            sys.exit(f"'{name}' 예약 실패: {(r.stderr or r.stdout).strip()}")
        out(f"예약됨: {name} (평일 {at[:5]})")
    out("")
    out("PC 를 완전히 끄지 말고 절전 상태로 두세요. 예약 시각에 깨워서 실행하고 끝나면 다시 잠들어요.")
    out("결과는 trading/logs/scheduled.txt 에 쌓여요.")
    out("깨우기가 안 되면: 제어판 → 전원 옵션 → 설정 변경 → 고급 전원 관리 → 절전 → '절전 모드 해제 타이머 허용'을 '사용'으로.")


def unregister(run=subprocess.run, out=print):
    for name in TASKS:
        r = run(["schtasks", "/Delete", "/TN", name, "/F"], capture_output=True, text=True, errors="replace")
        out(f"{'지움' if r.returncode == 0 else '없음'}: {name}")
