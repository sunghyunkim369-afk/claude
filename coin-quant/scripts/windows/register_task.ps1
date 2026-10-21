# coin-quant 매일 예약 실행 등록 (Windows 작업 스케줄러)
# 사용: PowerShell 에서  powershell -ExecutionPolicy Bypass -File scripts\windows\register_task.ps1 [-Time 09:10]
# 지우기: Unregister-ScheduledTask -TaskName "coin-quant daily" -Confirm:$false
param(
  [string]$Time = "09:10",          # KST 09:00 일봉 마감 직후
  [string]$TaskName = "coin-quant daily"
)
$root = (Resolve-Path "$PSScriptRoot\..\..").Path
$bat  = Join-Path $root "scripts\windows\run_daily.bat"
$action   = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$bat`"" -WorkingDirectory $root
$trigger  = New-ScheduledTaskTrigger -Daily -At $Time
# 컴퓨터가 꺼져 있어 놓치면 켜졌을 때 바로 실행, 절전이면 깨워서 실행, 1시간 넘으면 중단
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
            -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
# 현재 사용자 권한으로 (관리자 권한 불필요). 로그인 안 했을 때도 돌리려면 작업 스케줄러에서 '사용자의 로그온 여부에 관계없이 실행'을 켜세요.
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
  -Description "coin-quant: 데이터 갱신 + 일일 리포트 (주문은 하지 않음)" -Force | Out-Null
Write-Host "등록 완료: '$TaskName' 매일 $Time 실행 → 결과 reports\, 로그 logs\daily.log"
Write-Host "주의: 예약 실행은 리포트만 만듭니다. 주문은 직접 'python -m coinquant trade' 로 확인하며 실행하세요."
