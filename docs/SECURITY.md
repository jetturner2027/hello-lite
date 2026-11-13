# Security model

Face Unlock is a **convenience feature, not biometric security**. It uses an
ordinary RGB webcam. It cannot reliably tell a live face from a good enough
video replay, a 3D mask, or an identical twin. If an attacker matters to you,
don't use it: lock with your password.

## What it protects against

| Threat | Mitigation |
|---|---|
| Printed photo or phone photo held up to the camera | Passive MiniFASNet liveness on every voted frame (3 of 5 must pass). Phone photos scored 0.00 vs a 0.5 threshold in testing on this laptop |
| Video replay *(only with `[liveness] challenge = true`; off by default)* | Random head-turn challenge: turn in the *requested* direction, then return to frontal, with the frontal face re-matched. A client that omits the direction is refused |
| Photo or person held up beside you | A second face at least 60% of your face's width (`detect.rival_ratio`) fails immediately. Smaller faces, such as people farther back in a busy room, are ignored |
| Swapping faces mid-challenge | Same rival rule each frame, plus a face center jumping more than one face width between frames fails |
| Brute-forcing with many photos/attempts | 5 consecutive non-environmental failures disable face unlock until a non-face unlock (password/PIN). The counter persists across service restarts; a corrupt counter file fails closed |
| Unelevated malware in your session reading the stored password or face data | Both are DPAPI machine-scope encrypted **and** in files whose DACL allows SYSTEM only (Admins: delete only). Your normal session token matches neither ACE |
| Disk theft / copying the files elsewhere | DPAPI machine scope: blobs only decrypt on this Windows install |
| A fake service squatting the pipe name | Service creates the pipe with `FILE_FLAG_FIRST_PIPE_INSTANCE` and refuses to run if the name is taken. The CP checks `GetNamedPipeServerProcessId` → token is LocalSystem → image is exactly `C:\Program Files\FaceUnlock\python\python.exe` before sending anything |
| Non-LogonUI processes asking the service to verify | Pipe DACL is SYSTEM-only; the service additionally requires the client image to be `LogonUI.exe` (unless `allow_test_clients` is set for phase-4 testing) |
| Stale/replayed responses | 32-byte random nonce per request (BCryptGenRandom), echoed and checked. Responses of the wrong type or length are rejected |
| Service impersonating LogonUI's SYSTEM token | CP opens the pipe with `SECURITY_SQOS_PRESENT | SECURITY_IDENTIFICATION` |
| Using face to get past boot logon / UAC | Windows 10+ shows the lock screen as `CPUS_LOGON`, the same scenario as boot logon, so the CP checks its own session through WTS and only shows a tile when that session has a logged-on user and is locked; anything else, or an unknown state, means no tile. It never takes part in UAC/CredUI. The service separately refuses unless it has seen the console session lock (fails closed after service restart) |
| Face right after a reboot | Face unlock is off after every boot (and service restart) until you unlock once with your password/PIN. Windows' automatic restart sign-in logs you in and locks, so without this the first screen after a reboot would be a face-unlockable lock screen. Attempts before that never open the camera |
| Stored password became wrong (you changed it) | First rejected logon reports to the service, which disables face unlock, so it can't run up bad-password counts toward an account lockout |
| Being locked out | The password tile is never hidden (no credential provider filter). The PIN/password tile stays the default; the face tile becomes default and auto-submits only in the moment right after a background verify succeeded. See RECOVERY.md |
| Automatic attempts (no click needed) | While the lock screen is up, the CP checks in the background after new keyboard/mouse input or the display turning on, never in the first 2 s after locking. At most 4 tries per lock screen, 3 s apart. "No face" is the only result that allows another try; any other failure (no match, spoof, two faces) ends automatic tries for that lock screen, so a stranger or a photo gets one free attempt per lock. Off in challenge mode |

## Known limitations (honest list)

- **Default is look-and-go (no head turn).** By default, the only liveness
  defense is the passive model. A good enough video of your face, played on a
  screen held at the camera, may unlock the laptop if MiniFASNet misjudges it.
  The 5-failure lockout limits how many tries that gets. Set
  `[liveness] challenge = true` to also require a random head turn.
- **Replay/3D attacks with the challenge on**: someone at the machine sees the
  prompt, so two prepared clips (one per direction) could still pass, and
  nothing RGB-only catches good masks.
- **Elevated admin = game over**. Any elevated process can take ownership of
  the secret files, or simply run as SYSTEM and call DPAPI. On a single-user
  machine where you are admin, UAC elevation is the real boundary.
- **The stored password exists**. Face unlock works by replaying your real
  Windows password. Anyone who gets SYSTEM gets that password, which for a
  Microsoft account is also your online password. Consider a local account.
- **Python memory**: the service can't reliably wipe the password from memory
  (it never needs to: only the CP, in C++, reads and wipes it), but face
  embeddings live briefly in Python memory during a verify.
- **Blink detection** isn't implemented: YuNet gives 5 landmarks, not eyelids.
  The optional active challenge is head turn only.
- **Backlight hurts liveness.** With a window behind you, real-face scores fell to
  0.55-0.70 (vs ~1.00 in normal light). `camera.face_exposure` lengthens the real
  camera exposure when your face is dark, rather than lowering the threshold or
  brightening pixels in software. A photo held up in the same scene gets the same
  treatment, so the spoof test must include a backlit run.
- **Passive threshold is 0.5, and only phone photos were tested.** On this laptop
  (2026-11-10) phone photos scored 0.00 on all 75 frames from both models, at full
  and half screen brightness. A real face scored median 0.72, because the V2 model
  (2.7x crop) is unsure about real faces here (median 0.38) while V1SE is confident
  (0.99). Hence 0.5, not upstream's 0.7. Video replay on a good screen, prints and
  masks are untested; 0.5 is more forgiving of them than 0.7 would be.
- **Automatic attempts happen without you choosing to.** Anyone who wakes the
  locked screen gets one face check for free. If you lock with Win+L and stay
  seated, the first touch of the keyboard or mouse after 2 s unlocks you.
- **Third-party remote desktop tools** (TeamViewer, AnyDesk, …) aren't detected
  by the presence monitor; list their processes in `presence.pause_processes`.
- **Presence lock** treats any face as "present", so someone else sitting at
  your desk keeps it unlocked. It is a walk-away timer, not access control.
- The CP's server check compares an image path; a SYSTEM-level attacker could
  run that same `python.exe` with other code. That attacker already owns the box.
- `match.threshold` is still OpenCV's reference value, not benchmarked on this camera.

## Data handled

| Data | Where | Protection |
|---|---|---|
| Face embeddings (128 floats × ~15) | `%ProgramData%\FaceUnlock\secrets\gallery.bin` | DPAPI machine + SYSTEM-only ACL |
| Windows password | `%ProgramData%\FaceUnlock\secrets\credential.bin` | DPAPI machine + SYSTEM-only ACL |
| Failure counter | `%ProgramData%\FaceUnlock\secrets\limiter.json` | SYSTEM-only ACL (inherited) |
| Attempt log | `%ProgramData%\FaceUnlock\logs\attempts.log` | timestamp, result, reason, frame count, ms; never images, embeddings or scores |
| CP trace | `%ProgramData%\FaceUnlock\logs\cp.log` | event names and numbers only (scenario, status codes, triggers); no SIDs, names or passwords; restarts past 64 KB |
| Camera frames | memory only | never written to disk by any component |
| Dev gallery (phase 1 only) | `%LOCALAPPDATA%\FaceUnlock-dev\gallery.bin` | DPAPI *user* scope; the service never reads it. Delete it once phase 3 is in use |

## Models

All models are SHA-256 pinned in `face_unlock/models.py` and verified on every load.
- YuNet, SFace: from opencv_zoo at a pinned commit; hashes taken from the repo's Git LFS pointers.
- MiniFASNet V2 / V1SE: minivision-ai/Silent-Face-Anti-Spoofing (Apache-2.0) at a pinned
  commit, source `.pth` hashes pinned, converted locally by `tools/convert_minifasnet.py`
  with `torch.load(weights_only=True)`.
