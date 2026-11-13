from pathlib import Path

import numpy as np
import pytest

from face_unlock import secure_store as ss
from face_unlock.matching import EMBEDDING_DIM
from face_unlock.stores import (
    StoredCredential, deserialize_credential, deserialize_gallery, serialize_credential, serialize_gallery,
)

CRED = StoredCredential("LAPTOP", "jet", "S-1-5-21-1-2-3-1001", "pässwörd")


def test_gallery_roundtrip() -> None:
    e = np.random.default_rng(0).standard_normal((15, EMBEDDING_DIM)).astype(np.float32)
    g = deserialize_gallery(serialize_gallery(e))
    assert len(g) == 15


@pytest.mark.parametrize("mutate", [lambda b: b[:-4], lambda b: b"XXXX" + b[4:], lambda b: b + b"\0" * 4])
def test_gallery_rejects_corrupt(mutate) -> None:
    raw = serialize_gallery(np.ones((2, EMBEDDING_DIM), np.float32))
    with pytest.raises(ValueError):
        deserialize_gallery(mutate(raw))


def test_gallery_rejects_nan() -> None:
    e = np.ones((2, EMBEDDING_DIM), np.float32)
    e[0, 0] = np.nan
    with pytest.raises(ValueError):
        deserialize_gallery(serialize_gallery(e))


def test_credential_roundtrip_and_layout() -> None:
    raw = serialize_credential(CRED)
    assert raw[:4] == b"FUC1" and raw[4:8] == (6).to_bytes(4, "little")  # "LAPTOP"
    assert deserialize_credential(raw) == CRED


def test_credential_repr_hides_password() -> None:
    assert "pässwörd" not in repr(CRED)


@pytest.mark.parametrize("raw", [b"FUC1", b"FUC1" + b"\xff" * 4, serialize_credential(CRED) + b"x"])
def test_credential_rejects_corrupt(raw: bytes) -> None:
    with pytest.raises(ValueError):
        deserialize_credential(raw)


def test_credential_rejects_empty_field() -> None:
    with pytest.raises(ValueError):
        serialize_credential(StoredCredential("D", "", "S-1", "x"))


def test_dpapi_roundtrip_and_entropy_bound() -> None:
    blob = ss.protect(b"secret", b"entropy-a", machine=False)
    assert ss.unprotect(blob, b"entropy-a") == b"secret"
    with pytest.raises(Exception):
        ss.unprotect(blob, b"entropy-b")


def test_secret_file_is_unreadable_to_creator(tmp_path: Path) -> None:
    """The file is created already locked to SYSTEM: we (not SYSTEM) can write it but not read it back.

    Owner is omitted here because setting owner=Administrators needs elevation.
    """
    target = tmp_path / "secret.bin"
    ss.write_secret_file(target, b"data", sddl="D:P(A;;FA;;;SY)")
    assert target.exists()
    with pytest.raises(PermissionError):
        target.read_bytes()


def test_secret_file_can_be_replaced_with_delete_only_ace(tmp_path: Path) -> None:
    """Mirrors re-enrollment: the writer holds DELETE (BA's ACE in production) but never READ."""
    import win32api
    import win32security

    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32security.TOKEN_QUERY)
    me = win32security.ConvertSidToStringSid(win32security.GetTokenInformation(token, win32security.TokenUser)[0])
    sddl = f"D:P(A;;FA;;;SY)(A;;SD;;;{me})"
    target = tmp_path / "secret.bin"
    ss.write_secret_file(target, b"one", sddl=sddl)
    ss.write_secret_file(target, b"two", sddl=sddl)
    assert [p.name for p in tmp_path.iterdir()] == ["secret.bin"]  # no temp files left behind
    with pytest.raises(PermissionError):
        target.read_bytes()
