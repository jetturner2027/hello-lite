<#
Phase 3: install face_service as a LocalSystem Windows service. Elevated prompt required.

What it does, in order (run with -WhatIf first to see each step):
  1. Copies your Python 3.12 base install (without its site-packages) to
     C:\Program Files\FaceUnlock\python. The service must run from an
     admin-only folder: if it ran from C:\dev, your unelevated account
     could edit code that runs as SYSTEM.
  2. pip-installs the pinned requirements and this package into that copy.
  3. Copies hash-pinned models to C:\Program Files\FaceUnlock\models.
  4. Creates %ProgramData%\FaceUnlock (SYSTEM+Admins full, Users read) and
     a default config.toml if none exists.
  5. sc.exe create FaceUnlockSvc (auto start, LocalSystem, restart on failure).
  6. Starts it.
Undo: .\scripts\uninstall_service.ps1
#>
[CmdletBinding(SupportsShouldProcess)]
param()
$ErrorActionPreference = 'Stop'

$principal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Run elevated.' }

$repo = Split-Path $PSScriptRoot -Parent
$root = Join-Path $env:ProgramFiles 'FaceUnlock'
$py = Join-Path $root 'python\python.exe'
$data = Join-Path $env:ProgramData 'FaceUnlock'
$basePy = (& py -3.12 -c 'import sys; print(sys.base_prefix)').Trim()
$svc = 'FaceUnlockSvc'

function Step($target, $what, [scriptblock]$do) {
    if ($PSCmdlet.ShouldProcess($target, $what)) { & $do; if ($LASTEXITCODE) { throw "failed: $what" } }
}

Step "$root\python" "robocopy $basePy (excluding Lib\site-packages, Scripts)" {
    robocopy $basePy "$root\python" /E /XD (Join-Path $basePy 'Lib\site-packages') (Join-Path $basePy 'Scripts') /NFL /NDL /NJH /NJS | Out-Null
    if ($LASTEXITCODE -lt 8) { $global:LASTEXITCODE = 0 }  # robocopy: <8 = success
}
Step $py 'ensurepip + pip install -r requirements.txt + this package' {
    & $py -m ensurepip --default-pip | Out-Null
    & $py -m pip install --disable-pip-version-check --no-warn-script-location -r "$repo\requirements.txt"
    & $py -m pip install --disable-pip-version-check --no-warn-script-location --no-deps $repo
}
Step "$root\models" 'copy *.onnx (hashes are verified again on every load)' {
    New-Item -ItemType Directory -Force "$root\models" | Out-Null
    Copy-Item "$repo\models\*.onnx" "$root\models\" -Force
}
Step $data 'create, ACL SYSTEM/Admins full, Users read; default config.toml' {
    New-Item -ItemType Directory -Force $data | Out-Null
    icacls $data /inheritance:r /grant:r 'SYSTEM:(OI)(CI)F' 'Administrators:(OI)(CI)F' 'Users:(OI)(CI)RX' | Out-Null
    if (-not (Test-Path "$data\config.toml")) { Copy-Item "$repo\config.example.toml" "$data\config.toml" }
}
# New-Service, not sc.exe create: Windows PowerShell 5.1 mangles embedded quotes in
# native-command arguments, which broke the quoted binPath. New-Service passes the
# string straight to CreateService. It defaults to LocalSystem.
Step $svc "New-Service -BinaryPathName '`"$py`" -m face_unlock.service' (auto start, LocalSystem)" {
    New-Service -Name $svc -BinaryPathName "`"$py`" -m face_unlock.service" -StartupType Automatic `
        -DisplayName 'Face Unlock Service' -Description 'Webcam face verification for the Face Unlock credential provider.' | Out-Null
    sc.exe failure $svc reset= 86400 actions= restart/5000/restart/30000//
}
Step $svc 'start service' { sc.exe start $svc }
if (-not $WhatIfPreference) { Write-Host "Done. Check: sc.exe query $svc ; logs in $data\logs" }
