<#
Phase 2: start the walk-away lock monitor at logon, for the current user only.
No admin needed. Creates one shortcut in your Startup folder; nothing else.

    .\scripts\install_presence.ps1 -WhatIf     # show what it would do
    .\scripts\install_presence.ps1
Undo with .\scripts\uninstall_presence.ps1
#>
[CmdletBinding(SupportsShouldProcess)]
param([switch]$DryRun)  # monitor logs "would lock" instead of locking
$ErrorActionPreference = 'Stop'

$repo = Split-Path $PSScriptRoot -Parent
$pythonw = Join-Path $repo '.venv\Scripts\pythonw.exe'
if (-not (Test-Path $pythonw)) { throw "venv not found at $pythonw" }
$lnk = Join-Path ([Environment]::GetFolderPath('Startup')) 'FaceUnlock Presence.lnk'
$monitorArgs = '-m face_unlock.presence_monitor' + ($(if ($DryRun) { ' --dry-run' } else { '' }))

if ($PSCmdlet.ShouldProcess($lnk, "create shortcut: `"$pythonw`" $monitorArgs")) {
    $sh = New-Object -ComObject WScript.Shell
    $s = $sh.CreateShortcut($lnk)
    $s.TargetPath = $pythonw
    $s.Arguments = $monitorArgs
    $s.WorkingDirectory = $repo
    $s.Save()
    Write-Host "Installed. It starts at next logon; to start now: & `"$pythonw`" $monitorArgs"
}
