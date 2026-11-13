<#
Unregister the Face Unlock credential provider. Elevated prompt required.
Removing the Credential Providers key is what matters: LogonUI stops loading the
DLL from the next lock screen on. The DLL file may still be locked by a
running LogonUI; if so it's renamed and deleted at next reboot.
#>
[CmdletBinding(SupportsShouldProcess)]
param()
$ErrorActionPreference = 'Stop'

$guid = '{ff4cb556-aa39-4f71-8c84-bd1ad97b9dc1}'
$principal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Run elevated.' }

$cpKey = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Authentication\Credential Providers\$guid"
$clsidKey = "HKLM:\SOFTWARE\Classes\CLSID\$guid"
$dll = Join-Path $env:ProgramFiles 'FaceUnlock\FaceUnlockCP.dll'

if ((Test-Path $cpKey) -and $PSCmdlet.ShouldProcess($cpKey, 'delete key (LogonUI stops loading the provider)')) {
    Remove-Item -Path $cpKey -Recurse -Force
}
if ((Test-Path $clsidKey) -and $PSCmdlet.ShouldProcess($clsidKey, 'delete COM registration')) {
    Remove-Item -Path $clsidKey -Recurse -Force
}
if ((Test-Path $dll) -and $PSCmdlet.ShouldProcess($dll, 'delete DLL')) {
    try {
        Remove-Item $dll -Force
    } catch {
        $old = "$dll.$([guid]::NewGuid().ToString('N')).old"
        Rename-Item $dll $old
        Add-Type -Namespace W -Name K -MemberDefinition '[DllImport("kernel32.dll", CharSet=CharSet.Unicode)] public static extern bool MoveFileEx(string a, string b, int f);'
        [W.K]::MoveFileEx($old, $null, 4) | Out-Null  # MOVEFILE_DELAY_UNTIL_REBOOT
        Write-Host "DLL in use; renamed to $old and scheduled for deletion at reboot."
    }
}
if (-not $WhatIfPreference) { Write-Host 'Credential provider removed.' }
