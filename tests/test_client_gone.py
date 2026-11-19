"""The service notices when LogonUI hangs up mid-verify (real Windows named pipe)."""
from __future__ import annotations

import os

import win32file
import win32pipe

from face_unlock.service import client_gone


def _pair() -> tuple[int, int]:
    name = rf"\\.\pipe\FaceUnlockTest-{os.getpid()}"
    server = win32pipe.CreateNamedPipe(
        name, win32pipe.PIPE_ACCESS_DUPLEX, win32pipe.PIPE_TYPE_MESSAGE | win32pipe.PIPE_WAIT, 1, 64, 64, 0, None
    )
    client = win32file.CreateFile(name, win32file.GENERIC_READ | win32file.GENERIC_WRITE, 0, None,
                                  win32file.OPEN_EXISTING, 0, None)
    return server, client


def test_connected_client_is_not_gone() -> None:
    server, client = _pair()
    try:
        assert not client_gone(server)
    finally:
        client.Close()
        server.Close()


def test_closed_client_is_gone() -> None:
    server, client = _pair()
    client.Close()
    try:
        assert client_gone(server)
    finally:
        server.Close()


def test_unread_request_does_not_look_like_a_hang_up() -> None:
    server, client = _pair()
    try:
        win32file.WriteFile(client, b"x" * 40)  # the request sits unread in the pipe
        assert not client_gone(server)
    finally:
        client.Close()
        server.Close()
