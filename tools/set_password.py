"""Store the Windows password the credential provider submits after a face match.

Run from an elevated prompt. The password is read with getpass (never echoed,
never an argument, so it isn't in shell history or the process list), checked
once with LogonUser so a typo isn't stored, then written encrypted
(machine-scope DPAPI) to a SYSTEM-only file.

    python tools/set_password.py                       # current user, local account
    python tools/set_password.py --domain MicrosoftAccount --user you@example.com

If you change your Windows password, re-run this. Until then, the CP's first
rejected logon disables face unlock (so it can't burn through a lockout policy).
"""
from __future__ import annotations

import argparse
import ctypes
import getpass
import os
import sys

import pywintypes
import win32api
import win32con
import win32security

from face_unlock.msaccount import local_sid_for_microsoft_account
from face_unlock.stores import StoredCredential, save_credential


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", default=os.environ.get("USERNAME", ""))
    ap.add_argument("--domain", default=win32api.GetComputerName())
    args = ap.parse_args()

    if not ctypes.windll.shell32.IsUserAnAdmin():
        print("Run this from an elevated prompt.", file=sys.stderr)
        return 1

    try:
        sid_obj, _, _ = win32security.LookupAccountName(None, f"{args.domain}\\{args.user}")
    except pywintypes.error as e:
        print(f"unknown account {args.domain}\\{args.user}: {e.strerror}", file=sys.stderr)
        return 1
    sid = win32security.ConvertSidToStringSid(sid_obj)
    if args.domain.casefold() == "microsoftaccount":
        # That lookup gave the online identity SID (S-1-11-96-...); LogonUI knows the
        # user by the linked local account's SID, so the tile would never match.
        local = local_sid_for_microsoft_account(args.user)
        if local is None:
            print(f"no single local account is linked to {args.user}; nothing stored.", file=sys.stderr)
            return 1
        sid = local
    if not sid.startswith("S-1-5-21-"):
        print(f"{sid} is not a local/domain user SID; nothing stored.", file=sys.stderr)
        return 1

    pw = getpass.getpass(f"Windows password for {args.domain}\\{args.user}: ")
    if pw != getpass.getpass("Again: "):
        print("Passwords differ; nothing stored.", file=sys.stderr)
        return 1
    try:
        token = win32security.LogonUser(
            args.user, args.domain, pw, win32con.LOGON32_LOGON_INTERACTIVE, win32con.LOGON32_PROVIDER_DEFAULT
        )
        token.Close()
    except pywintypes.error as e:
        print(f"Windows rejected that password ({e.strerror}); nothing stored.", file=sys.stderr)
        return 1

    path = save_credential(StoredCredential(args.domain, args.user, sid, pw))
    del pw  # best effort; CPython can't guarantee the string is wiped
    print(f"stored for {args.domain}\\{args.user} ({sid}) at {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
