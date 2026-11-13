<#
Run one repo Python script once as SYSTEM in session 0, via a temporary
scheduled task that is deleted afterwards. Needs an elevated prompt.

Used for the phase 3 camera test and the phase 4 pipe test. It runs code
from this repo (a user-writable folder) as SYSTEM, which is fine for a
one-off test you start yourself and NOT something to leave registered.

    .\scripts\run_as_system.ps1 tools\session0_camera_test.py -WhatIf
    .\scripts\run_as_system.ps1 tools\pipe_client.py verify --direction left
Output lands in %ProgramData%\FaceUnlock\logs\run_as_system.txt
#>
[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory, Position = 0)][string]$Script,
    [Parameter(ValueFromRemainingArguments)][string[]]$ScriptArgs
)
$ErrorActionPreference = 'Stop'

$repo = Split-Path $PSScriptRoot -Parent
$python = Join-Path $repo '.venv\Scripts\python.exe'
$logDir = Join-Path $env:ProgramData 'FaceUnlock\logs'
$log = Join-Path $logDir 'run_as_system.txt'
$task = 'FaceUnlock-RunAsSystem-Test'
# /s: strip exactly the outer quotes. /v:on: !ERRORLEVEL! is read after python exits,
# not when cmd parses the line. The markers show whether cmd and python ran at all.
$inner = "echo [start %DATE% %TIME% as %USERNAME%] >> `"$log`" & `"$python`" `"$Script`" $($ScriptArgs -join ' ') >> `"$log`" 2>&1 & echo [python exit !ERRORLEVEL!] >> `"$log`""
$cmdline = "/v:on /s /c `"$inner`""

if (-not $PSCmdlet.ShouldProcess("scheduled task '$task' as SYSTEM", "cmd.exe $cmdline")) { return }

# Battery/energy saver (on by default below 20%) holds back scheduled tasks no matter what
# the task settings say: the run is recorded as started but nothing executes. Seen 2026-11-10.
Add-Type -Namespace FaceUnlock -Name Power -MemberDefinition @'
[StructLayout(LayoutKind.Sequential)] public struct SYSTEM_POWER_STATUS {
    public byte ACLineStatus, BatteryFlag, BatteryLifePercent, SystemStatusFlag;
    public int BatteryLifeTime, BatteryFullLifeTime; }
[DllImport("kernel32.dll")] public static extern bool GetSystemPowerStatus(out SYSTEM_POWER_STATUS s);
'@ -ErrorAction SilentlyContinue
$ps = New-Object FaceUnlock.Power+SYSTEM_POWER_STATUS
if ([FaceUnlock.Power]::GetSystemPowerStatus([ref]$ps) -and ($ps.SystemStatusFlag -band 1)) {
    Write-Warning "Battery saver is on ($($ps.BatteryLifePercent)%): the task will likely not run. Plug in the charger first."
}

New-Item -ItemType Directory -Force $logDir | Out-Null
$before = if (Test-Path $log) { (Get-Item $log).Length } else { 0 }
$action = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument $cmdline -WorkingDirectory $repo
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
# Task defaults refuse to start on battery, silently: the run "happens" but nothing executes.
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
Register-ScheduledTask -TaskName $task -Action $action -Principal $principal -Settings $settings -Force | Out-Null
try {
    $started = Get-Date
    Start-ScheduledTask -TaskName $task
    $deadline = $started.AddSeconds(90)
    # Wait until Task Scheduler records that this run began, THEN until it stops.
    # Checking only for 'Running' races: a task that hasn't started yet reads 'Ready'.
    do {
        Start-Sleep -Milliseconds 500
        $info = Get-ScheduledTaskInfo -TaskName $task
        $state = (Get-ScheduledTask -TaskName $task).State
    } while (($state -eq 'Running' -or $info.LastRunTime -lt $started.AddSeconds(-1)) -and (Get-Date) -lt $deadline)
    if ((Get-Date) -ge $deadline) { Write-Warning 'timed out waiting for the task' }
    Write-Host "task exit code: $($info.LastTaskResult)  last run: $($info.LastRunTime)  (started $started)"
    Write-Host "cmd.exe $cmdline"
} finally {
    Unregister-ScheduledTask -TaskName $task -Confirm:$false
}
# Show only what this run appended.
if (Test-Path $log) {
    $fs = [System.IO.File]::Open($log, 'Open', 'Read', 'ReadWrite')
    try { $fs.Seek($before, 'Begin') | Out-Null; $new = (New-Object System.IO.StreamReader($fs)).ReadToEnd() } finally { $fs.Dispose() }
    if ($new -notmatch '\[start ') { Write-Warning 'The task never executed (no [start] marker). Check power/task conditions.' }
    $new
}
