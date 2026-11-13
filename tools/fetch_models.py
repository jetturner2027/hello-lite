"""Download YuNet and SFace into models/ and verify their pinned SHA-256.

MiniFASNet isn't downloaded; see tools/convert_minifasnet.py.
"""
from __future__ import annotations

import sys
import urllib.request

from face_unlock.integrity import IntegrityError, sha256_file
from face_unlock.models import ALL_MODELS, models_dir


def main() -> int:
    out = models_dir()
    out.mkdir(parents=True, exist_ok=True)
    status = 0
    for spec in ALL_MODELS:
        dst = out / spec.filename
        if spec.url is None:
            state = "ok" if dst.is_file() and sha256_file(dst) == spec.sha256 else "missing/unpinned"
            print(f"{spec.filename}: produced locally ({state}); run tools/convert_minifasnet.py")
            continue
        if dst.is_file() and sha256_file(dst) == spec.sha256:
            print(f"{spec.filename}: ok")
            continue
        tmp = dst.with_suffix(".part")
        print(f"{spec.filename}: downloading")
        with urllib.request.urlopen(spec.url, timeout=60) as resp, tmp.open("wb") as f:
            while chunk := resp.read(1 << 20):
                f.write(chunk)
        actual = sha256_file(tmp)
        if actual != spec.sha256:
            tmp.unlink()
            print(f"  HASH MISMATCH: got {actual}", file=sys.stderr)
            status = 1
            continue
        tmp.replace(dst)
        print("  verified")
    return status


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except IntegrityError as e:
        print(e, file=sys.stderr)
        raise SystemExit(1)
