<#
Phase 5: register the Face Unlock credential provider. Elevated prompt required.

Do this in a VM first. On real hardware, read docs\RECOVERY.md first.
The script refuses to run unless:
  - FaceUnlockSvc is installed and running
  - a stored credential exists (tools\set_password.py ran)
  - another enabled local administrator account exists besides you
  - docs\RECOVERY.md exists

Exact changes (also shown by -WhatIf):
  copy  build\Release\FaceUnlockCP.dll -> C:\Program Files\FaceUnlock\FaceUnlockCP.dll
  HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Authentication\Credential Providers\{GUID}  (Default)=FaceUnlock
  HKLM\SOFTWARE\Classes\CLSID\{GUID}                (Default)=FaceUnlock
  HKLM\SOFTWARE\Classes\CLSID\{GUID}\InprocServer32 (Default)=<dll path>, ThreadingModel=Apartment
Undo: .\credential_provider\uninstall.ps1
#>
[CmdletBinding(SupportsShouldProcess)]
param()
$ErrorActionPreference = 'Stop'

$guid = '{ff4cb556-aa39-4f71-8c84-bd1ad97b9dc1}'
$principal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Run elevated.' }

$repo = Split-Path $PSScriptRoot -Parent
$src = Join-Path $PSScriptRoot 'build\Release\FaceUnlockCP.dll'
$dst = Join-Path $env:ProgramFiles 'FaceUnlock\FaceUnlockCP.dll'
$cpKey = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Authentication\Credential Providers\$guid"
$clsidKey = "HKLM:\SOFTWARE\Classes\CLSID\$guid"

# --- preflight -----------------------------------------------------------------
if (-not (Test-Path $src)) { throw "Build first: $src not found." }
if ((Get-Service FaceUnlockSvc -ErrorAction SilentlyContinue).Status -ne 'Running') { throw 'FaceUnlockSvc is not running.' }
if (-not (Test-Path (Join-Path $env:ProgramData 'FaceUnlock\secrets\credential.bin'))) { throw 'No stored credential; run tools\set_password.py first.' }
if (-not (Test-Path (Join-Path $repo 'docs\RECOVERY.md'))) { throw 'docs\RECOVERY.md missing.' }
$me = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$otherAdmins = Get-LocalGroupMember -SID 'S-1-5-32-544' | Where-Object { $_.PrincipalSource -eq 'Local' -and $_.ObjectClass -eq 'User' -and $_.SID.Value -ne $me } |
    Where-Object { (Get-LocalUser -SID $_.SID).Enabled }
if (-not $otherAdmins) { throw 'No second enabled local admin account. Create one first (see docs\RECOVERY.md).' }
Write-Host "Preflight ok. Backup admin(s): $($otherAdmins.Name -join ', ')"

# --- install -------------------------------------------------------------------
if ($PSCmdlet.ShouldProcess($dst, "copy $src")) { Copy-Item $src $dst -Force }
if ($PSCmdlet.ShouldProcess($clsidKey, "(Default)=FaceUnlock; InprocServer32=$dst; ThreadingModel=Apartment")) {
    New-Item -Path "$clsidKey\InprocServer32" -Force | Out-Null
    Set-Item -Path $clsidKey -Value 'FaceUnlock'
    Set-Item -Path "$clsidKey\InprocServer32" -Value $dst
    New-ItemProperty -Path "$clsidKey\InprocServer32" -Name ThreadingModel -Value 'Apartment' -Force | Out-Null
}
if ($PSCmdlet.ShouldProcess($cpKey, '(Default)=FaceUnlock  <- this line is what makes LogonUI load the DLL')) {
    New-Item -Path $cpKey -Force | Out-Null
    Set-Item -Path $cpKey -Value 'FaceUnlock'
}
if (-not $WhatIfPreference) { Write-Host 'Registered. Lock with Win+L to see the tile under Sign-in options.' }
