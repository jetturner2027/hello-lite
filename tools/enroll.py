"""Enroll your face: capture ~15 varied samples and store them encrypted.

    python tools/enroll.py --dev     # phase 1: per-user store, no admin
    python tools/enroll.py           # system store for the service (elevated)

A preview window shows you (mirrored, like a mirror). Move your head slightly
between samples: a little left/right/up/down. Near-duplicate samples are
skipped so the gallery covers some pose variety.
"""
from __future__ import annotations

import argparse
import ctypes
import sys
import time

import cv2
import numpy as np

from face_unlock.camera import Camera, CameraError
from face_unlock.config import load_config
from face_unlock.detection import FaceDetector, FaceEmbedder, single_face
from face_unlock.matching import l2_normalize
from face_unlock.stores import save_gallery

DUPLICATE_SIM = 0.92  # a new sample this similar to an existing one adds nothing
OUTLIER_SIM = 0.45  # a sample this dissimilar to the rest is probably not you
MIN_INTERVAL_S = 0.25


def consistent(embeddings: np.ndarray) -> np.ndarray:
    """Mask of samples whose mean similarity to the others clears OUTLIER_SIM."""
    e = l2_normalize(embeddings)
    sims = e @ e.T
    n = len(e)
    mean_other = (sims.sum(axis=1) - 1.0) / max(1, n - 1)
    return mean_other >= OUTLIER_SIM


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dev", action="store_true", help="per-user dev store (phase 1)")
    ap.add_argument("--samples", type=int, default=15)
    args = ap.parse_args()

    if not args.dev and not ctypes.windll.shell32.IsUserAnAdmin():
        print("The system store needs an elevated prompt (or use --dev).", file=sys.stderr)
        return 1

    cfg = load_config()
    det, emb = FaceDetector(cfg.detect.score_threshold), FaceEmbedder()
    samples: list[np.ndarray] = []
    last = 0.0
    try:
        with Camera(cfg.camera) as cam:
            while len(samples) < args.samples:
                frame = cam.read()
                if frame is None:
                    print("camera read failed", file=sys.stderr)
                    return 1
                faces = det.detect(frame)
                face = single_face(faces, cfg.detect.min_face_px)
                status = f"{len(samples)}/{args.samples}"
                color = (0, 0, 255)
                if len(faces) > 1:
                    status += "  one face only, please"
                elif face is None:
                    status += "  move closer / into frame"
                elif time.monotonic() - last >= MIN_INTERVAL_S:
                    e = l2_normalize(emb.embed(frame, face))
                    if samples and max(float(s @ e) for s in samples) >= DUPLICATE_SIM:
                        status += "  move your head slightly"
                    else:
                        samples.append(e)
                        last = time.monotonic()
                        color = (0, 200, 0)
                if face is not None:
                    x, y, w, h = map(int, face.box)
                    cv2.rectangle(frame, (x, y), (x + w, y + h), color, 2)
                view = cv2.flip(frame, 1)
                cv2.putText(view, status, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)
                cv2.imshow("Face Unlock enrollment (Esc to cancel)", view)
                if cv2.waitKey(1) == 27:
                    print("cancelled; nothing saved")
                    return 1
    except CameraError as e:
        print(e, file=sys.stderr)
        return 1
    finally:
        cv2.destroyAllWindows()

    gallery = np.stack(samples)
    keep = consistent(gallery)
    if keep.sum() < max(5, args.samples // 2):
        print("Samples are inconsistent (another person in view?). Nothing saved; try again.", file=sys.stderr)
        return 1
    path = save_gallery(gallery[keep], dev=args.dev)
    print(f"saved {int(keep.sum())} samples to {path} (dropped {int((~keep).sum())} outliers)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
