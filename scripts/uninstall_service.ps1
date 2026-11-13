<#
Remove face_service. Elevated prompt required.
  -RemoveData also deletes %ProgramData%\FaceUnlock (enrolled face, stored password, logs).
Refuses to run while the credential provider is still registered, since the
CP would be left pointing at a missing service (it fails closed, but still).
#>
[CmdletBinding(SupportsShouldProcess)]
param([switch]$RemoveData)
$ErrorActionPreference = 'Stop'

$principal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Run elevated.' }

$cpKey = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Authentication\Credential Providers\{ff4cb556-aa39-4f71-8c84-bd1ad97b9dc1}'
if (Test-Path $cpKey) { throw 'Credential provider still registered; run credential_provider\uninstall.ps1 first.' }

$svc = 'FaceUnlockSvc'
$root = Join-Path $env:ProgramFiles 'FaceUnlock'
$data = Join-Path $env:ProgramData 'FaceUnlock'

if ((Get-Service $svc -ErrorAction SilentlyContinue) -and $PSCmdlet.ShouldProcess($svc, 'sc.exe stop + delete')) {
    sc.exe stop $svc | Out-Null
    $deadline = (Get-Date).AddSeconds(30)
    while ((Get-Service $svc).Status -ne 'Stopped' -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 500 }
    sc.exe delete $svc | Out-Null
}
if ((Test-Path $root) -and $PSCmdlet.ShouldProcess($root, 'remove python runtime, package, models')) {
    Remove-Item -Recurse -Force (Join-Path $root 'python'), (Join-Path $root 'models') -ErrorAction SilentlyContinue
    if (-not (Get-ChildItem $root -ErrorAction SilentlyContinue)) { Remove-Item $root }
}
if ($RemoveData -and (Test-Path $data) -and $PSCmdlet.ShouldProcess($data, 'take ownership and delete (secrets, logs, config)')) {
    # Secret files are SYSTEM-only by design; take ownership so Admins can delete them.
    takeown /f $data /r /a /d y | Out-Null
    icacls $data /grant 'Administrators:(OI)(CI)F' /t /c | Out-Null
    Remove-Item -Recurse -Force $data
}
Write-Host 'Service removed.'
