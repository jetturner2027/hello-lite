"""Phase 4 test client: does exactly what the credential provider does, from Python.

Must run as SYSTEM (the pipe DACL admits nobody else), and the service needs
[service] allow_test_clients = true because this isn't LogonUI. See
scripts/run_as_system.ps1.

    python tools/pipe_client.py status
    python tools/pipe_client.py verify --direction left
"""
from __future__ import annotations

import argparse
import os
import secrets
import sys
import time
from pathlib import Path

import pywintypes
import win32api
import win32con
import win32file
import win32pipe
import win32security

from face_unlock import winapi
from face_unlock.liveness import Direction
from face_unlock.protocol import MESSAGE_LEN, PIPE_NAME, MsgType, Request, encode_request, parse_response

SECURITY_SQOS_PRESENT = 0x00100000
SECURITY_IDENTIFICATION = 0x00010000
EXPECTED_SERVER = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "FaceUnlock" / "python" / "python.exe"


def server_is_trusted(pipe: int) -> tuple[bool, str]:
    pid = win32pipe.GetNamedPipeServerProcessId(pipe)
    proc = win32api.OpenProcess(winapi.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    try:
        token = win32security.OpenProcessToken(proc, win32con.TOKEN_QUERY)
        user_sid = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
    finally:
        proc.Close()
    if user_sid != win32security.CreateWellKnownSid(win32security.WinLocalSystemSid):
        return False, f"server pid {pid} is not SYSTEM"
    image = winapi.process_image_path(pid) or ""
    if os.path.normcase(image) != os.path.normcase(str(EXPECTED_SERVER)):
        return False, f"server pid {pid} image {image!r} != {EXPECTED_SERVER}"
    return True, f"server pid {pid} ok"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("type", choices=["status", "verify"])
    ap.add_argument("--direction", choices=["left", "right", "none"], default="none")
    ap.add_argument("--delay", type=float, default=0.0, help="seconds to wait first, so you can press Win+L")
    args = ap.parse_args()
    time.sleep(args.delay)  # the service only verifies while the console session is locked

    # SQOS identification level: the server can identify us but never impersonate
    # our SYSTEM token. The CP opens the pipe the same way.
    try:
        pipe = win32file.CreateFile(
            PIPE_NAME, win32file.GENERIC_READ | win32file.GENERIC_WRITE, 0, None, win32file.OPEN_EXISTING,
            SECURITY_SQOS_PRESENT | SECURITY_IDENTIFICATION, None,
        )
    except pywintypes.error as e:
        print(f"cannot open pipe: {e.strerror}", file=sys.stderr)
        return 1
    try:
        win32pipe.SetNamedPipeHandleState(pipe, win32pipe.PIPE_READMODE_MESSAGE, None, None)
        ok, msg = server_is_trusted(pipe)
        print(msg)
        if not ok:
            return 1
        mtype = MsgType.VERIFY if args.type == "verify" else MsgType.STATUS
        direction = None if args.direction == "none" else Direction[args.direction.upper()]
        if direction:
            print(f"Turn your head to your {direction.name} and back.")
        nonce = secrets.token_bytes(32)
        win32file.WriteFile(pipe, encode_request(Request(mtype, nonce, direction)))
        _, data = win32file.ReadFile(pipe, MESSAGE_LEN)
        resp = parse_response(bytes(data), nonce, mtype)
        print(f"{resp.status.name} {resp.reason.name} challenge_required={resp.challenge_required}")
        return 0
    finally:
        pipe.Close()


if __name__ == "__main__":
    raise SystemExit(main())
