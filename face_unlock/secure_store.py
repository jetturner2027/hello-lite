"""DPAPI encryption plus SYSTEM-only file ACLs for secrets at rest.

Two layers, because each covers the other's gap:
- DPAPI *machine* scope binds the blob to this machine, so a copied file or a
  disk pulled out of the laptop is useless. But any process on the machine can
  decrypt machine-scope blobs, so on its own it doesn't keep the user out.
- The file DACL lets only SYSTEM read. That's what keeps your normal
  (unelevated) session out. An elevated admin can always take ownership and
  get in; on Windows that's unavoidable, and docs/SECURITY.md says so.

Files are created *with* their final DACL (CreateFile + SECURITY_ATTRIBUTES)
into a temp name and then renamed over the target, so there's never a moment
where the secret sits in a file with inherited, looser permissions.

The rename goes through the creating handle (SetFileInformationByHandle),
because once the file exists, its own DACL won't let us reopen it. A newly
created file grants its creator whatever access was requested at creation.
"""
from __future__ import annotations

import ctypes
import os
import secrets as _secrets
from ctypes import wintypes
from pathlib import Path

import pywintypes
import win32con
import win32crypt
import win32file
import win32security

from face_unlock.config import data_dir

CRYPTPROTECT_UI_FORBIDDEN = 0x1
CRYPTPROTECT_LOCAL_MACHINE = 0x4

# Secret files: SYSTEM full control. Administrators get DELETE only ("SD"), so an
# elevated tool can replace the file but not read it. "P" = protected, i.e.
# ignore inherited ACEs. Your unelevated session matches neither ACE.
SECRET_FILE_SDDL = "O:BAD:P(A;;FA;;;SY)(A;;SD;;;BA)"
# Secrets directory: SYSTEM full (inherited by children); Administrators may
# list, add and replace files (0x1000E3 = LIST_DIRECTORY | ADD_FILE | TRAVERSE |
# DELETE_CHILD | READ_ATTRIBUTES | SYNCHRONIZE) but get no read access to contents.
SECRETS_DIR_SDDL = "O:BAD:P(A;OICI;FA;;;SY)(A;;0x1000E3;;;BA)"


def secrets_dir() -> Path:
    return data_dir() / "secrets"


def dev_dir() -> Path:
    """Phase-1 dev store: per-user, user-scope DPAPI. The service never reads it."""
    return Path(os.environ.get("LOCALAPPDATA", ".")) / "FaceUnlock-dev"


def protect(data: bytes, entropy: bytes, machine: bool) -> bytes:
    flags = CRYPTPROTECT_UI_FORBIDDEN | (CRYPTPROTECT_LOCAL_MACHINE if machine else 0)
    return win32crypt.CryptProtectData(data, None, entropy, None, None, flags)


def unprotect(blob: bytes, entropy: bytes) -> bytes:
    _, data = win32crypt.CryptUnprotectData(blob, entropy, None, None, CRYPTPROTECT_UI_FORBIDDEN)
    return data


def _sa(sddl: str) -> pywintypes.SECURITY_ATTRIBUTES:
    sa = pywintypes.SECURITY_ATTRIBUTES()
    sa.SECURITY_DESCRIPTOR = win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(
        sddl, win32security.SDDL_REVISION_1
    )
    sa.bInheritHandle = False
    return sa


def ensure_secrets_dir(path: Path | None = None, sddl: str = SECRETS_DIR_SDDL) -> Path:
    path = path or secrets_dir()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        win32file.CreateDirectory(str(path), _sa(sddl))
    else:
        sd = win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(sddl, win32security.SDDL_REVISION_1)
        win32security.SetFileSecurity(
            str(path),
            win32security.DACL_SECURITY_INFORMATION | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
            sd,
        )
    return path


_FILE_RENAME_INFO_CLASS = 3
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.SetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
_kernel32.SetFileInformationByHandle.restype = wintypes.BOOL


def _rename_by_handle(handle: int, target: Path) -> None:
    name = str(target)
    # FILE_RENAME_INFO on x64: BOOLEAN ReplaceIfExists (padded to 8) | HANDLE RootDirectory |
    # DWORD FileNameLength (bytes) | WCHAR FileName[]
    class FILE_RENAME_INFO(ctypes.Structure):
        _fields_ = [
            ("ReplaceIfExists", wintypes.BOOLEAN),
            ("RootDirectory", wintypes.HANDLE),
            ("FileNameLength", wintypes.DWORD),
            ("FileName", wintypes.WCHAR * (len(name) + 1)),
        ]

    info = FILE_RENAME_INFO(True, None, len(name) * 2, name)
    if not _kernel32.SetFileInformationByHandle(int(handle), _FILE_RENAME_INFO_CLASS, ctypes.byref(info), ctypes.sizeof(info)):
        raise ctypes.WinError(ctypes.get_last_error())


def write_secret_file(path: Path, data: bytes, sddl: str = SECRET_FILE_SDDL) -> None:
    tmp = path.with_name(f"{path.name}.{_secrets.token_hex(4)}.tmp")
    handle = win32file.CreateFile(
        str(tmp), win32con.GENERIC_WRITE | win32con.DELETE, 0, _sa(sddl), win32con.CREATE_NEW,
        win32con.FILE_ATTRIBUTE_NORMAL, None,
    )
    try:
        try:
            win32file.WriteFile(handle, data)
            win32file.FlushFileBuffers(handle)
            _rename_by_handle(handle, path)
        except BaseException:
            # Mark the temp file for deletion on close; we can't reopen it to delete later.
            win32file.SetFileInformationByHandle(handle, win32file.FileDispositionInfo, True)
            raise
    finally:
        handle.Close()


def write_user_file(path: Path, data: bytes) -> None:
    """Dev store write: inherits the user's profile ACL."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)
