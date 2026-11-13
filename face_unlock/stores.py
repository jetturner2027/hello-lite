"""On-disk formats for the enrolled gallery and the stored unlock credential.

Both are small fixed binary layouts (not pickle, not JSON) so the C++
credential provider can parse the credential file without a JSON library,
and so loading never executes anything.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from face_unlock import secure_store as ss
from face_unlock.matching import EMBEDDING_DIM, Gallery

GALLERY_MAGIC = b"FUG1"
GALLERY_ENTROPY = b"FaceUnlock.Gallery.v1"
CREDENTIAL_MAGIC = b"FUC1"
# Must match credential_provider/src/cred_store.cpp.
CREDENTIAL_ENTROPY = b"FaceUnlock.Credential.v1"

MAX_GALLERY = 256
MAX_FIELD_CHARS = 512


def gallery_path(dev: bool) -> Path:
    return (ss.dev_dir() if dev else ss.secrets_dir()) / "gallery.bin"


def credential_path() -> Path:
    return ss.secrets_dir() / "credential.bin"


# --- gallery ---------------------------------------------------------------

def serialize_gallery(embeddings: np.ndarray) -> bytes:
    arr = np.ascontiguousarray(embeddings, dtype="<f4")
    if arr.ndim != 2 or arr.shape[1] != EMBEDDING_DIM or not 1 <= arr.shape[0] <= MAX_GALLERY:
        raise ValueError(f"bad gallery shape {arr.shape}")
    return GALLERY_MAGIC + struct.pack("<I", arr.shape[0]) + arr.tobytes()


def deserialize_gallery(data: bytes) -> Gallery:
    if len(data) < 8 or data[:4] != GALLERY_MAGIC:
        raise ValueError("not a gallery file")
    (n,) = struct.unpack_from("<I", data, 4)
    if not 1 <= n <= MAX_GALLERY or len(data) != 8 + n * EMBEDDING_DIM * 4:
        raise ValueError("corrupt gallery file")
    arr = np.frombuffer(data, dtype="<f4", offset=8).reshape(n, EMBEDDING_DIM)
    if not np.all(np.isfinite(arr)):
        raise ValueError("corrupt gallery file")
    return Gallery(arr)


def save_gallery(embeddings: np.ndarray, dev: bool) -> Path:
    path = gallery_path(dev)
    blob = ss.protect(serialize_gallery(embeddings), GALLERY_ENTROPY, machine=not dev)
    if dev:
        ss.write_user_file(path, blob)
    else:
        ss.ensure_secrets_dir()
        ss.write_secret_file(path, blob)
    return path


def load_gallery(dev: bool) -> Gallery:
    return deserialize_gallery(ss.unprotect(gallery_path(dev).read_bytes(), GALLERY_ENTROPY))


# --- credential --------------------------------------------------------------

@dataclass(frozen=True)
class StoredCredential:
    domain: str  # computer name for a local account
    user: str
    sid: str  # S-1-5-21-...; the CP shows its tile only for this user
    password: str

    def __repr__(self) -> str:  # never let the password reach a log or traceback
        return f"StoredCredential(domain={self.domain!r}, user={self.user!r}, sid={self.sid!r}, password=***)"


def serialize_credential(c: StoredCredential) -> bytes:
    """FUC1 | (u32 char count, UTF-16LE chars) x 4: domain, user, sid, password."""
    out = bytearray(CREDENTIAL_MAGIC)
    for value in (c.domain, c.user, c.sid, c.password):
        if not 1 <= len(value) <= MAX_FIELD_CHARS or "\0" in value:
            raise ValueError("credential field empty, too long, or contains NUL")
        encoded = value.encode("utf-16-le")
        out += struct.pack("<I", len(encoded) // 2) + encoded
    return bytes(out)


def deserialize_credential(data: bytes) -> StoredCredential:
    if data[:4] != CREDENTIAL_MAGIC:
        raise ValueError("not a credential file")
    pos, fields = 4, []
    for _ in range(4):
        if pos + 4 > len(data):
            raise ValueError("truncated credential file")
        (n,) = struct.unpack_from("<I", data, pos)
        pos += 4
        if not 1 <= n <= MAX_FIELD_CHARS or pos + 2 * n > len(data):
            raise ValueError("corrupt credential file")
        fields.append(data[pos : pos + 2 * n].decode("utf-16-le"))
        pos += 2 * n
    if pos != len(data):
        raise ValueError("trailing data in credential file")
    return StoredCredential(*fields)


def save_credential(c: StoredCredential) -> Path:
    path = credential_path()
    ss.ensure_secrets_dir()
    ss.write_secret_file(path, ss.protect(serialize_credential(c), CREDENTIAL_ENTROPY, machine=True))
    return path
