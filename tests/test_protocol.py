import struct

import pytest

from face_unlock.liveness import Direction
from face_unlock.protocol import (
    FLAG_CHALLENGE, MESSAGE_LEN, MsgType, ProtocolError, Request, Response, Status, encode_request, encode_response,
    parse_request, parse_response,
)
from face_unlock.verifier import Reason

NONCE = bytes(range(1, 33))


def test_request_roundtrip() -> None:
    req = Request(MsgType.VERIFY, NONCE, Direction.RIGHT)
    raw = encode_request(req)
    assert len(raw) == MESSAGE_LEN
    assert parse_request(raw) == req


def test_response_roundtrip_and_layout() -> None:
    raw = encode_response(Response(MsgType.VERIFY, Status.FAIL, Reason.SPOOF, NONCE))
    assert raw[:8] == b"FUr1" + bytes([1, 1, 4, 0])  # layout the C++ side depends on
    assert parse_response(raw, NONCE, MsgType.VERIFY).reason is Reason.SPOOF


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"x" * 39,
        b"x" * 41,
        struct.pack("<4sBBH32s", b"XXXX", 1, 0, 0, NONCE),  # magic
        struct.pack("<4sBBH32s", b"FUq1", 9, 0, 0, NONCE),  # type
        struct.pack("<4sBBH32s", b"FUq1", 1, 3, 0, NONCE),  # direction
        struct.pack("<4sBBH32s", b"FUq1", 1, 0, 1, NONCE),  # reserved
        struct.pack("<4sBBH32s", b"FUq1", 3, 1, 0, NONCE),  # direction on STATUS
        struct.pack("<4sBBH32s", b"FUq1", 1, 0, 0, bytes(32)),  # zero nonce
    ],
)
def test_parse_request_rejects(raw: bytes) -> None:
    with pytest.raises(ProtocolError):
        parse_request(raw)


def test_response_with_wrong_nonce_rejected() -> None:
    raw = encode_response(Response(MsgType.VERIFY, Status.OK, Reason.NONE, NONCE))
    with pytest.raises(ProtocolError, match="nonce"):
        parse_response(raw, bytes(reversed(NONCE)), MsgType.VERIFY)


def test_response_with_wrong_type_rejected() -> None:
    raw = encode_response(Response(MsgType.STATUS, Status.OK, Reason.NONE, NONCE))
    with pytest.raises(ProtocolError, match="type"):
        parse_response(raw, NONCE, MsgType.VERIFY)


def test_challenge_flag_roundtrip_and_layout() -> None:
    raw = encode_response(Response(MsgType.STATUS, Status.OK, Reason.NONE, NONCE, FLAG_CHALLENGE))
    assert raw[7] == 1  # flags byte, which the C++ side reads
    assert parse_response(raw, NONCE, MsgType.STATUS).challenge_required
    assert not parse_response(
        encode_response(Response(MsgType.STATUS, Status.OK, Reason.NONE, NONCE)), NONCE, MsgType.STATUS
    ).challenge_required


def test_unknown_flag_bits_rejected() -> None:
    raw = struct.pack("<4sBBBB32s", b"FUr1", 3, 0, 0, 0x80, NONCE)
    with pytest.raises(ProtocolError):
        parse_response(raw, NONCE, MsgType.STATUS)
    with pytest.raises(ProtocolError):
        encode_response(Response(MsgType.STATUS, Status.OK, Reason.NONE, NONCE, 0x80))


def test_response_unknown_status_rejected() -> None:
    raw = struct.pack("<4sBBBB32s", b"FUr1", 1, 77, 0, 0, NONCE)
    with pytest.raises(ProtocolError):
        parse_response(raw, NONCE, MsgType.VERIFY)
