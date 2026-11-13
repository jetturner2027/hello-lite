"""Map a Microsoft account email to the local account it is linked to.

LookupAccountName("MicrosoftAccount\\you@example.com") returns the online identity
SID (S-1-11-96-...). LogonUI identifies the user by the *local* account SID
(S-1-5-21-...), so the credential provider needs that one. NetUserGetInfo level 24
(USER_INFO_24) reports, per local account, the linked internet principal name
and the local SID. pywin32 doesn't expose level 24, hence ctypes.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes

import win32net
import win32security


class _USER_INFO_24(ctypes.Structure):
    _fields_ = [
        ("internet_identity", wintypes.BOOL),
        ("flags", wintypes.DWORD),
        ("internet_provider_name", wintypes.LPWSTR),
        ("internet_principal_name", wintypes.LPWSTR),
        ("user_sid", ctypes.c_void_p),
    ]


_netapi = ctypes.WinDLL("netapi32")
_netapi.NetUserGetInfo.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)]
_netapi.NetUserGetInfo.restype = wintypes.DWORD
_netapi.NetApiBufferFree.argtypes = [ctypes.c_void_p]
_advapi = ctypes.WinDLL("advapi32")
_advapi.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
_kernel = ctypes.WinDLL("kernel32")
_kernel.LocalFree.argtypes = [ctypes.c_void_p]


def _linked_identity(local_user: str) -> tuple[str, str] | None:
    """(internet principal name, local SID string) for a linked account, else None."""
    buf = ctypes.c_void_p()
    if _netapi.NetUserGetInfo(None, local_user, 24, ctypes.byref(buf)) != 0 or not buf:
        return None
    try:
        info = ctypes.cast(buf, ctypes.POINTER(_USER_INFO_24)).contents
        if not info.internet_identity or not info.internet_principal_name or not info.user_sid:
            return None
        s = wintypes.LPWSTR()
        if not _advapi.ConvertSidToStringSidW(info.user_sid, ctypes.byref(s)):
            return None
        try:
            return info.internet_principal_name, s.value or ""
        finally:
            _kernel.LocalFree(s)
    finally:
        _netapi.NetApiBufferFree(buf)


def local_sid_for_microsoft_account(email: str) -> str | None:
    """Local S-1-5-21-... SID of the account linked to this Microsoft account, if exactly one."""
    users, _, _ = win32net.NetUserEnum(None, 0)
    hits = []
    for u in users:
        ident = _linked_identity(u["name"])
        if ident and ident[0].casefold() == email.casefold():
            hits.append(ident[1])
    if len(hits) != 1 or not hits[0].startswith("S-1-5-21-"):
        return None
    # Belt and braces: the SID must resolve to a real local user.
    _, _, kind = win32security.LookupAccountSid(None, win32security.ConvertStringSidToSid(hits[0]))
    return hits[0] if kind == 1 else None  # SidTypeUser
