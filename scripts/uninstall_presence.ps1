<#
Remove the walk-away lock monitor: deletes the Startup shortcut and stops any running instance.
#>
[CmdletBinding(SupportsShouldProcess)]
param()
$ErrorActionPreference = 'Stop'

$lnk = Join-Path ([Environment]::GetFolderPath('Startup')) 'FaceUnlock Presence.lnk'
if ((Test-Path $lnk) -and $PSCmdlet.ShouldProcess($lnk, 'delete shortcut')) { Remove-Item $lnk }

$procs = Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe'" |
    Where-Object { $_.CommandLine -like '*face_unlock.presence_monitor*' }
foreach ($p in $procs) {
    if ($PSCmdlet.ShouldProcess("pid $($p.ProcessId)", 'stop presence monitor')) {
        Stop-Process -Id $p.ProcessId -Force
    }
}
Write-Host 'Presence monitor removed.'
