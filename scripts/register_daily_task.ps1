# Registers (or replaces) the per-user scheduled task that runs scripts\collect_daily.cmd daily.
# Daily candles close at 00:00 UTC (8 PM EDT / 7 PM EST), so 9 PM local is after the close all year.
# No admin rights needed; runs only while you are logged on. Missed runs start when the PC is next available.
# Remove with: Unregister-ScheduledTask -TaskName "cryptoAgent daily collect" -Confirm:$false
param([string]$At = "9:00PM")

$script = Join-Path $PSScriptRoot "collect_daily.cmd"
$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$script`""
$trigger = New-ScheduledTaskTrigger -Daily -At $At
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2)
Register-ScheduledTask -TaskName "cryptoAgent daily collect" -Action $action -Trigger $trigger -Settings $settings `
    -Description "Crypto agent: daily public candle collection and quality checks (no AI)" -Force | Out-Null
Get-ScheduledTask -TaskName "cryptoAgent daily collect" | Select-Object TaskName, State
