# Recovery: if the lock screen breaks

Read this **before** installing the credential provider on real hardware.

## Before you install (required)

1. **Second local admin account.** `credential_provider\install.ps1` refuses to run without one.
   ```powershell
   # elevated; pick your own name and a strong password you'll remember or store offline
   net user RecoveryAdmin * /add
   net localgroup Administrators RecoveryAdmin /add
   ```
   Sign in to it once so its profile exists.
2. **Know your password.** Don't rely on PIN/Hello only. Make sure
   *Settings → Accounts → Sign-in options → "For improved security, only allow
   Windows Hello sign-in"* is **off**, or the password tile may be hidden.
3. **BitLocker recovery key.** If the drive is encrypted, have the recovery key
   (aka.ms/myrecoverykey for Microsoft accounts) on another device. You need it
   to use WinRE offline registry editing.
4. Test the whole thing in a VM first.

## Level 1: the tile misbehaves but the lock screen works

The password tile is always present. Click **Sign-in options** → key icon, sign
in with your password, then from an elevated prompt:
```powershell
cd C:\dev\face-unlock
.\credential_provider\uninstall.ps1
```

Quick disable without uninstalling (verified in the Windows 11 test VM,
2026-11-10: the tile disappears from the next lock screen):
```powershell
reg add "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Authentication\Credential Providers\{ff4cb556-aa39-4f71-8c84-bd1ad97b9dc1}" /v Disabled /t REG_DWORD /d 1 /f
```

## Level 2: you can't get past the lock screen in your session

- Press Ctrl+Alt+Del → **Switch user**, sign in as `RecoveryAdmin` (that's a
  logon, which the provider ignores entirely), and run the Level 1 commands.
- Or reboot. After a restart face unlock is off until you unlock once with your
  password, so the tile can't get in your way. If Windows signed you in
  automatically after the restart, the face tile may still be listed, but it
  only says it's off; at a true sign-in screen it isn't shown at all.

## Level 3: LogonUI itself crashes or hangs (a bug in the DLL)

**Safe Mode:**
1. On the sign-in screen, hold **Shift** and click **Power → Restart**.
   (If you can't reach it: power off during boot three times to trigger WinRE.)
2. Troubleshoot → Advanced options → Startup Settings → Restart → **4** (Safe Mode).
3. Sign in with your password or `RecoveryAdmin`, then run the Level 1 commands.

**If Safe Mode also fails: offline registry edit from WinRE**
1. WinRE → Troubleshoot → Advanced options → **Command Prompt**
   (unlock BitLocker with the recovery key if asked).
2. Find the Windows drive; it's often `D:` in WinRE: `dir D:\Windows`.
3. ```cmd
   reg load HKLM\OFFSW D:\Windows\System32\config\SOFTWARE
   reg delete "HKLM\OFFSW\Microsoft\Windows\CurrentVersion\Authentication\Credential Providers\{ff4cb556-aa39-4f71-8c84-bd1ad97b9dc1}" /f
   reg delete "HKLM\OFFSW\Classes\CLSID\{ff4cb556-aa39-4f71-8c84-bd1ad97b9dc1}" /f
   reg unload HKLM\OFFSW
   ```
4. Close the prompt and **Continue** to Windows. LogonUI will no longer load the DLL.

## Face unlock just says it's disabled

Expected after every restart (until your first password/PIN unlock), after
5 failed attempts, or after Windows rejected the stored password. Unlock once
with your password/PIN; that re-arms it. If you changed your Windows password,
re-run `tools\set_password.py` (elevated).

## Removing everything

```powershell
# elevated, in this order
.\credential_provider\uninstall.ps1
.\scripts\uninstall_service.ps1 -RemoveData
.\scripts\uninstall_presence.ps1          # not elevated is fine
Remove-Item -Recurse $env:LOCALAPPDATA\FaceUnlock-dev, $env:LOCALAPPDATA\FaceUnlock
```
