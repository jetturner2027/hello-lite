"""Pipe wire format between the credential provider and face_service.

Fixed-size little-endian structs, no variable-length fields: the C++ side
parses with one length check and a memcpy, which is much harder to get wrong
than a JSON parser inside LogonUI. Mirror: credential_provider/src/protocol.h.

Request  (40 bytes): magic "FUq1" | type u8 | direction u8 | reserved u16 | nonce[32]
Response (40 bytes): magic "FUr1" | type u8 | status u8 | reason u8 | flags u8 | nonce[32]

flags bit 0 (FLAG_CHALLENGE): the service requires a head-turn direction in VERIFY,
so the tile knows whether to show "turn LEFT/RIGHT". Other bits must be zero.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import IntEnum

from face_unlock.liveness import Direction
from face_unlock.verifier import Reason

PIPE_NAME = r"\\.\pipe\FaceUnlock"
REQUEST_MAGIC = b"FUq1"
RESPONSE_MAGIC = b"FUr1"
NONCE_LEN = 32
_REQ = struct.Struct("<4sBBH32s")
_RESP = struct.Struct("<4sBBBB32s")
MESSAGE_LEN = _REQ.size
assert _RESP.size == MESSAGE_LEN == 40
FLAG_CHALLENGE = 0x01
_KNOWN_FLAGS = FLAG_CHALLENGE


class ProtocolError(Exception):
    pass


class MsgType(IntEnum):
    VERIFY = 1
    REPORT_LOGON_FAILED = 2  # CP: Windows rejected the stored password
    STATUS = 3


class Status(IntEnum):
    OK = 0
    FAIL = 1
    DISABLED = 2
    ERROR = 3


_DIRECTIONS = {0: None, 1: Direction.LEFT, 2: Direction.RIGHT}


@dataclass(frozen=True)
class Request:
    type: MsgType
    nonce: bytes
    direction: Direction | None = None


@dataclass(frozen=True)
class Response:
    type: MsgType
    status: Status
    reason: Reason
    nonce: bytes
    flags: int = 0

    @property
    def challenge_required(self) -> bool:
        return bool(self.flags & FLAG_CHALLENGE)


def parse_request(data: bytes) -> Request:
    if len(data) != MESSAGE_LEN:
        raise ProtocolError(f"bad length {len(data)}")
    magic, mtype, direction, reserved, nonce = _REQ.unpack(data)
    if magic != REQUEST_MAGIC or reserved != 0:
        raise ProtocolError("bad magic or reserved field")
    try:
        msg_type = MsgType(mtype)
    except ValueError:
        raise ProtocolError(f"unknown type {mtype}") from None
    if direction not in _DIRECTIONS:
        raise ProtocolError(f"unknown direction {direction}")
    if msg_type is not MsgType.VERIFY and direction != 0:
        raise ProtocolError("direction only valid for VERIFY")
    if nonce == bytes(NONCE_LEN):
        raise ProtocolError("all-zero nonce")  # catches a client that forgot to fill it
    return Request(msg_type, nonce, _DIRECTIONS[direction])


def encode_request(req: Request) -> bytes:
    if len(req.nonce) != NONCE_LEN:
        raise ProtocolError("nonce must be 32 bytes")
    d = {v: k for k, v in _DIRECTIONS.items()}[req.direction]
    return _REQ.pack(REQUEST_MAGIC, req.type, d, 0, req.nonce)


def encode_response(resp: Response) -> bytes:
    if resp.flags & ~_KNOWN_FLAGS:
        raise ProtocolError("unknown flag bits")
    return _RESP.pack(RESPONSE_MAGIC, resp.type, resp.status, resp.reason, resp.flags, resp.nonce)


def parse_response(data: bytes, expected_nonce: bytes, expected_type: MsgType) -> Response:
    """Client-side parse. A response for anyone else's nonce is rejected."""
    if len(data) != MESSAGE_LEN:
        raise ProtocolError(f"bad length {len(data)}")
    magic, mtype, status, reason, flags, nonce = _RESP.unpack(data)
    if magic != RESPONSE_MAGIC or flags & ~_KNOWN_FLAGS:
        raise ProtocolError("bad magic or unknown flags")
    if nonce != expected_nonce:
        raise ProtocolError("nonce mismatch")
    if mtype != expected_type:
        raise ProtocolError("type mismatch")
    try:
        return Response(MsgType(mtype), Status(status), Reason(reason), nonce, flags)
    except ValueError:
        raise ProtocolError("unknown status or reason") from None
