"""Phase 3 gate: can a SYSTEM process in session 0 open the webcam?

Run as SYSTEM via scripts/test_session0_camera.ps1. Writes a one-line result
(no images) to %ProgramData%\\FaceUnlock\\logs\\session0_camera.txt.

Windows 10/11 camera privacy settings and Frame Server can block session-0
capture; this is the riskiest unknown in the design, so we test it first.
"""
from __future__ import annotations

import datetime as dt
import os
import time

import cv2

from face_unlock.config import data_dir, load_config
from face_unlock.detection import FaceDetector


def main() -> int:
    cfg = load_config()
    out = data_dir() / "logs"
    out.mkdir(parents=True, exist_ok=True)
    who = os.environ.get("USERNAME", "?")
    t0 = time.perf_counter()
    cap = cv2.VideoCapture(cfg.camera.index, cv2.CAP_DSHOW)
    opened = cap.isOpened()
    frames = faces = 0
    mean = 0.0
    if opened:
        det = FaceDetector(cfg.detect.score_threshold)
        for _ in range(15):
            ok, frame = cap.read()
            if ok:
                frames += 1
                mean = float(frame.mean())  # all-black frames => privacy shutter / blocked stream
                faces += bool(det.detect(frame))
    cap.release()
    line = (f"{dt.datetime.now().isoformat(timespec='seconds')} user={who} opened={opened} "
            f"frames={frames}/15 frames_with_face={faces} last_mean_brightness={mean:.1f} "
            f"elapsed_ms={(time.perf_counter() - t0) * 1000:.0f}\n")
    with (out / "session0_camera.txt").open("a", encoding="utf-8") as f:
        f.write(line)
    return 0 if opened and frames else 1


if __name__ == "__main__":
    raise SystemExit(main())
