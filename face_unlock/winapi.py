"""Thin ctypes wrappers for the few Win32 calls pywin32 doesn't expose cleanly."""
from __future__ import annotations

import ctypes
from ctypes import wintypes

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

SM_REMOTESESSION = 0x1000
DESKTOP_READOBJECTS = 0x0001
UOI_NAME = 2
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


_user32.GetLastInputInfo.argtypes = [ctypes.POINTER(LASTINPUTINFO)]
_user32.GetLastInputInfo.restype = wintypes.BOOL
_user32.LockWorkStation.restype = wintypes.BOOL
_user32.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_user32.OpenInputDesktop.restype = wintypes.HANDLE
_user32.CloseDesktop.argtypes = [wintypes.HANDLE]
_user32.GetUserObjectInformationW.argtypes = [
    wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)
]
_kernel32.GetTickCount.restype = wintypes.DWORD
_kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_kernel32.OpenProcess.restype = wintypes.HANDLE
_kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)
]
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.WTSGetActiveConsoleSessionId.restype = wintypes.DWORD


def idle_seconds() -> float:
    """Seconds since the last keyboard/mouse input in this session.

    dwTime and GetTickCount are both 32-bit ms counters; unsigned subtraction
    mod 2^32 stays correct across the 49.7-day wraparound.
    """
    info = LASTINPUTINFO(ctypes.sizeof(LASTINPUTINFO), 0)
    if not _user32.GetLastInputInfo(ctypes.byref(info)):
        raise ctypes.WinError(ctypes.get_last_error())
    return ((_kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF) / 1000.0


def is_remote_session() -> bool:
    return bool(_user32.GetSystemMetrics(SM_REMOTESESSION))


def is_workstation_locked() -> bool:
    """True when the input desktop isn't our "Default" desktop (lock screen, UAC prompt).

    While locked, OpenInputDesktop fails with access denied because the input
    desktop is Winlogon's. Either way we must not lock or use the camera.
    """
    desk = _user32.OpenInputDesktop(0, False, DESKTOP_READOBJECTS)
    if not desk:
        return True
    try:
        buf = ctypes.create_unicode_buffer(64)
        needed = wintypes.DWORD()
        if not _user32.GetUserObjectInformationW(desk, UOI_NAME, buf, ctypes.sizeof(buf), ctypes.byref(needed)):
            return True
        return buf.value.lower() != "default"
    finally:
        _user32.CloseDesktop(desk)


def lock_workstation() -> None:
    if not _user32.LockWorkStation():
        raise ctypes.WinError(ctypes.get_last_error())


def active_console_session() -> int:
    return int(_kernel32.WTSGetActiveConsoleSessionId())


def process_image_path(pid: int) -> str | None:
    h = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return None
    try:
        size = wintypes.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        if not _kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return None
        return buf.value
    finally:
        _kernel32.CloseHandle(h)
