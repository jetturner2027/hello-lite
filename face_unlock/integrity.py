"""SHA-256 verification for model files before they are loaded."""
from __future__ import annotations

import hashlib
from pathlib import Path


class IntegrityError(Exception):
    pass


def sha256_file(path: Path, chunk_size: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(chunk_size):
            h.update(chunk)
    return h.hexdigest()


def verify_file(path: Path, expected_sha256: str) -> Path:
    """Return path if its hash matches; raise IntegrityError otherwise."""
    if not path.is_file():
        raise IntegrityError(f"missing model file: {path}")
    actual = sha256_file(path)
    if actual.lower() != expected_sha256.strip().lower():
        raise IntegrityError(f"hash mismatch for {path.name}: expected {expected_sha256}, got {actual}")
    return path
