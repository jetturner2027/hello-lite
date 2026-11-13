"""Phase 1 dev loop: run the full verify flow against the --dev gallery.

    python tools/verify.py                 # match + passive liveness + head turn
    python tools/verify.py --no-challenge
    python tools/verify.py --show-yaw      # live yaw readout, to check the turn sign/thresholds

Prints the result and per-phase timings. No images are saved.
"""
from __future__ import annotations

import argparse
import sys
import time
from dataclasses import replace

import cv2

from face_unlock.camera import Camera, CameraError, FrameSource
from face_unlock.config import load_config
from face_unlock.detection import Frame, FaceDetector, FaceEmbedder
from face_unlock.exposure import expose_for_face
from face_unlock.liveness import PassiveLiveness, random_direction, yaw_ratio
from face_unlock.stores import load_gallery
from face_unlock.verifier import Verifier

WINDOW = "Face Unlock verify"


class PreviewSource:
    """Wraps a frame source and shows each frame (mirrored) with a prompt. Dev tool only."""

    def __init__(self, inner: FrameSource, prompt: str) -> None:
        self._inner = inner
        self._prompt = prompt

    def read(self) -> Frame | None:
        frame = self._inner.read()
        if frame is not None:
            view = cv2.flip(frame, 1)
            cv2.putText(view, self._prompt, (15, 45), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 255), 2)
            cv2.imshow(WINDOW, view)
            cv2.waitKey(1)
        return frame


def show_message(text: str, seconds: float) -> None:
    import numpy as np

    canvas = np.zeros((480, 640, 3), np.uint8)
    cv2.putText(canvas, text, (20, 240), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2)
    cv2.imshow(WINDOW, canvas)
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        cv2.waitKey(30)


def show_yaw(cfg) -> int:
    det = FaceDetector(cfg.detect.score_threshold)
    with Camera(cfg.camera) as cam:
        end = time.monotonic() + 15
        while time.monotonic() < end:
            frame = cam.read()
            faces = det.detect(frame) if frame is not None else []
            if len(faces) == 1:
                print(f"\ryaw_ratio={yaw_ratio(faces[0]):+.2f}  (turn to YOUR left => positive)   ", end="")
    print()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-challenge", action="store_true")
    ap.add_argument("--show-yaw", action="store_true")
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--no-window", action="store_true", help="console only, no preview window")
    args = ap.parse_args()

    cfg = load_config()
    if args.show_yaw:
        return show_yaw(cfg)
    if args.no_challenge:
        cfg = replace(cfg, liveness=replace(cfg.liveness, challenge=False))
    gallery = load_gallery(dev=True)
    detector = FaceDetector(cfg.detect.score_threshold)
    verifier = Verifier(cfg, detector, FaceEmbedder(), PassiveLiveness())

    ok_all = True
    for i in range(args.runs):
        direction = random_direction() if cfg.liveness.challenge else None
        prompt = f"Look, then turn {direction.name} and back" if direction else "Look at the camera"
        print(f"[{i + 1}] {prompt}")
        if not args.no_window:
            show_message(f"Run {i + 1}/{args.runs}: {prompt}", 2.0)
        t0 = time.perf_counter()
        try:
            with Camera(cfg.camera) as cam:
                t_open = (time.perf_counter() - t0) * 1000
                if cfg.camera.face_exposure:
                    t1 = time.perf_counter()
                    exp = expose_for_face(cam, detector, cfg.camera)
                    # Grey levels only, never the image itself.
                    print(f"    face brightness {exp.initial} -> {exp.final}, "
                          f"exposure={'auto' if exp.exposure is None else exp.exposure} "
                          f"({(time.perf_counter() - t1) * 1000:.0f}ms)")
                source = cam if args.no_window else PreviewSource(cam, prompt)
                result = verifier.verify(source, gallery, direction)
        except CameraError as e:
            print(e, file=sys.stderr)
            return 1
        total = (time.perf_counter() - t0) * 1000
        timings = " ".join(f"{k}={v:.0f}ms" for k, v in result.timings_ms.items())
        print(f"    {'OK' if result.ok else 'FAIL'} {result.reason.name} frames={result.frames} "
              f"open={t_open:.0f}ms {timings} total={total:.0f}ms")
        ok_all &= result.ok
        if not args.no_window:
            show_message("OK" if result.ok else f"FAIL: {result.reason.name}", 1.0)
    cv2.destroyAllWindows()
    return 0 if ok_all else 2


if __name__ == "__main__":
    raise SystemExit(main())
