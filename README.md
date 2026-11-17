# hello-lite

**Face unlock for laptops that don't have Windows Hello hardware.**

Windows Hello face sign-in only works with an infrared (IR) camera, and most
budget and older laptops don't have one. hello-lite brings the same
glance-to-unlock experience to the ordinary 720p webcam those laptops already
have. You don't need to buy new hardware.

- **Glance and go:** wake the lock screen and look at it. It signs you in in
  about a second.
- **Walk-away lock:** leave your desk and it locks behind you.
- **Built for low-end hardware:** developed and benchmarked on an Intel Core
  i3-1005G1 with 8 GB RAM. A verify takes ~1.2 s. There's no TensorFlow or
  PyTorch, and nothing heavy stays in memory between unlocks.
- **Your PIN and password always work**, alongside face unlock, never replaced
  by it.

Not affiliated with Microsoft; "Windows Hello" is their trademark.

RGB-webcam face unlock and walk-away auto-lock for Windows 10/11. A lighter,
safer take on the ideas in caochitam/windows-face-unlock: OpenCV + ONNX
Runtime only (no TensorFlow/PyTorch at runtime), nothing resident beyond a
small idle service (the face models are preloaded only while the screen is
locked, and dropped on unlock), and unlock-only scope.

**This is convenience, not security.** RGB-only liveness can't stop a good
replay or mask. By default unlock is look-and-go: no head turn
(`[liveness] challenge = false`). See [docs/SECURITY.md](docs/SECURITY.md).

## Layout

```
face_unlock/          Python package
  detection.py        YuNet detect, SFace embed
  matching.py         gallery + top-k cosine match
  liveness.py         MiniFASNet passive + head-turn challenge
  voting.py           N-of-M frame voting
  verifier.py         the verify flow (all deps injected → testable)
  presence*.py        walk-away lock (user session)
  protocol.py         40-byte pipe messages
  service_core.py     request handling, rate limit, session tracking
  service.py          Windows service + hardened pipe
  secure_store.py     DPAPI + SYSTEM-only file ACLs
  stores.py           gallery/credential file formats
credential_provider/  C++ LogonUI tile (CMake, VS 2022)
tools/                enroll, verify, bench, set_password, model fetch/convert, test clients
scripts/              install/uninstall (all support -WhatIf)
docs/                 SECURITY.md, RECOVERY.md
```

## Phase 1: face pipeline (no admin)

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\pip install -r requirements-dev.txt
.\.venv\Scripts\pip install --no-deps -e .
.\.venv\Scripts\python tools\fetch_models.py          # YuNet + SFace, hash-verified
```

MiniFASNet (one-off, in a throwaway venv so torch never touches the runtime).
Keep the venv path short: `onnx` ships test files whose paths exceed Windows'
260-character limit under a deep folder, and a half-installed `onnx` breaks export.
```powershell
py -3.12 -m venv $env:USERPROFILE\fuconv
& $env:USERPROFILE\fuconv\Scripts\python -m pip install torch==2.5.1 --index-url https://download.pytorch.org/whl/cpu
& $env:USERPROFILE\fuconv\Scripts\python -m pip install onnx==1.17.0 numpy==1.26.4
# get MiniFASNet.py + the two .pth files from minivision-ai/Silent-Face-Anti-Spoofing
# @ b6d5f04ad78778917853b25c778acef6d5626d15 into one folder, then:
& $env:USERPROFILE\fuconv\Scripts\python -I tools\convert_minifasnet.py --upstream <folder> --out models
```
Paste the two printed hashes into `MINIFAS_V2` / `MINIFAS_V1SE` in
`face_unlock/models.py`. Until then, liveness refuses to load (fails closed).

Then:
```powershell
.\.venv\Scripts\python tools\bench.py --no-liveness     # before conversion
.\.venv\Scripts\python tools\enroll.py --dev            # ~15 samples, move your head a little
.\.venv\Scripts\python tools\verify.py --show-yaw       # check turn sign + thresholds
.\.venv\Scripts\python tools\verify.py --runs 10
.\.venv\Scripts\python tools\bench.py                   # full: latency per stage + peak RAM
.\.venv\Scripts\python -m pytest
```
Targets: verify < 2 s (excluding the human head turn), peak RAM a few hundred MB.

## Phase 2: presence lock (no admin)

```powershell
.\.venv\Scripts\python -m face_unlock.presence_monitor --dry-run   # watch %LOCALAPPDATA%\FaceUnlock\presence.log
.\scripts\install_presence.ps1 -WhatIf
.\scripts\install_presence.ps1
```

## Phase 3: SYSTEM service (admin)

First the gate: can session 0 open the camera?
```powershell
.\scripts\run_as_system.ps1 tools\session0_camera_test.py
```
If `opened=True frames=15/15` with non-zero brightness, continue:
```powershell
.\scripts\install_service.ps1 -WhatIf
.\scripts\install_service.ps1
.\.venv\Scripts\python tools\enroll.py      # elevated: system store
```

## Phase 4: pipe protocol (admin)

Set `[service] allow_test_clients = true` in `%ProgramData%\FaceUnlock\config.toml`,
restart the service, then:
```powershell
.\scripts\run_as_system.ps1 tools\pipe_client.py status
# verify only works while locked: start it, press Win+L within 10 s, turn your head LEFT and back
.\scripts\run_as_system.ps1 tools\pipe_client.py verify --direction left --delay 10
Get-Content $env:ProgramData\FaceUnlock\logs\attempts.log -Tail 5
```
Turn `allow_test_clients` back off when done.

## Phase 5: credential provider (admin, VM first)

Read [docs/RECOVERY.md](docs/RECOVERY.md). Then:
```powershell
cmake -S credential_provider -B credential_provider\build -G "Visual Studio 17 2022" -A x64
cmake --build credential_provider\build --config Release
credential_provider\build\Release\cp_tests.exe
python tools\set_password.py                 # elevated; local account
# Microsoft account: stores the linked local account's SID, which LogonUI matches on
python tools\set_password.py --domain MicrosoftAccount --user you@example.com
.\credential_provider\install.ps1 -WhatIf
.\credential_provider\install.ps1
```

On the lock screen the face check runs by itself: after you touch the keyboard or
mouse, or the screen turns on (not in the first 2 s after locking). It signs you in
when it recognises you; otherwise the PIN/password works as usual. The tile under
**Sign-in options** also runs a check on click. Trace: `%ProgramData%\FaceUnlock\logs\cp.log`.
