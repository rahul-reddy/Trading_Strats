# Registers a Windows Scheduled Task that runs the strategy every weekday near the US close.
# The task fires every 5 minutes from 15:30 to 15:50 ET (converted to this PC's local time);
# the runner itself only acts inside the [min, max] minutes-to-close window and handles early
# closes and holidays via the NYSE calendar. Each run is idempotent: once positions match the
# targets, later runs that day plan no orders.
#
#   .\scripts\register_task.ps1                  # dry-run mode (logs what it would do)
#   .\scripts\register_task.ps1 -Live            # sends orders (config also needs live_trading_enabled = true)
#   .\scripts\register_task.ps1 -Broker ibkr
#   Unregister-ScheduledTask -TaskName TQQQBlend  # remove it

param(
    [ValidateSet("paper", "ibkr", "schwab")] [string]$Broker = "paper",
    [switch]$Live
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = (Get-Command python).Source

# 15:30 ET in local time (handles whatever timezone this PC is in, and DST)
$et = [TimeZoneInfo]::FindSystemTimeZoneById("Eastern Standard Time")
$etStart = [DateTime]::SpecifyKind([DateTime]::Today.AddHours(15).AddMinutes(30), "Unspecified")
$localStart = [TimeZoneInfo]::ConvertTime($etStart, $et, [TimeZoneInfo]::Local)

$runArgs = "-m tqqq_blend run --broker $Broker"
if ($Live) { $runArgs += " --live" }

$action = New-ScheduledTaskAction -Execute $python -Argument $runArgs -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday -At $localStart
$trigger.Repetition = (New-ScheduledTaskTrigger -Once -At $localStart `
    -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration (New-TimeSpan -Minutes 21)).Repetition
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -ExecutionTimeLimit (New-TimeSpan -Minutes 10)

Register-ScheduledTask -TaskName "TQQQBlend" -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Write-Host "Registered TQQQBlend: weekdays from $($localStart.ToShortTimeString()) local, every 5 min x5, '$runArgs'"
Write-Host "Note: re-run this script after a DST change if this PC is not on US Eastern time."
