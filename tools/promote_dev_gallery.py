"""Copy the phase-1 dev enrollment into the service's system store, re-encrypted.

The dev gallery is user-scope DPAPI in %LOCALAPPDATA%; the service needs
machine-scope DPAPI in the SYSTEM-only secrets folder. An elevated process
still runs as you, so it can decrypt the user-scope blob and re-encrypt it.

Run from an elevated prompt. Caveat: the dev file lived in a user-writable
folder, so this trusts that nothing in your session replaced it. Re-run
tools/enroll.py (elevated) instead if that matters to you.
"""
from __future__ import annotations

import ctypes
import sys

from face_unlock.stores import gallery_path, load_gallery, save_gallery


def main() -> int:
    if not ctypes.windll.shell32.IsUserAnAdmin():
        print("Run this from an elevated prompt.", file=sys.stderr)
        return 1
    src = gallery_path(dev=True)
    if not src.is_file():
        print(f"no dev gallery at {src}; run tools/enroll.py --dev first", file=sys.stderr)
        return 1
    gallery = load_gallery(dev=True)
    dst = save_gallery(gallery.embeddings, dev=False)  # rows are normalized; re-normalizing is idempotent
    print(f"promoted {len(gallery)} samples: {src} -> {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
