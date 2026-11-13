"""Liveness calibration: per-frame MiniFASNet scores, as the service would see them.

    python tools/live_scores.py --label real       # you, in front of the camera
    python tools/live_scores.py --label photo      # a phone photo of you held up instead

Each run: an aiming preview (mirrored, with a green box on detected faces), then
cold camera opens like the service does (auto exposure, then face-priority
exposure if enabled), scoring the largest face. Prints numbers only: no frames
are saved, and the preview is never written anywhere.
"""
from __future__ import annotations

import argparse
import statistics
import time

import cv2

from face_unlock.camera import Camera
from face_unlock.config import load_config
from face_unlock.detection import FaceDetector
from face_unlock.exposure import expose_for_face
from face_unlock.liveness import PassiveLiveness

AIM_WINDOW = "aim"


def aim(cfg, det: FaceDetector, seconds: float) -> None:
    with Camera(cfg.camera) as cam:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            frame = cam.read()
            if frame is None:
                continue
            for face in det.detect(frame):
                x, y, w, h = map(int, face.box)
                cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 255, 0), 2)
            view = cv2.flip(frame, 1)
            cv2.putText(view, f"AIM: get a green box, then hold still  {int(end - time.monotonic())}s",
                        (10, 35), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
            cv2.imshow(AIM_WINDOW, view)
            cv2.waitKey(1)
    cv2.destroyAllWindows()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="sample", help="printed with the summary, e.g. real / photo / photo-backlit")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--frames", type=int, default=15)
    ap.add_argument("--aim", type=float, default=8.0, help="seconds of aiming preview; 0 to skip")
    args = ap.parse_args()

    cfg = load_config()
    det, live = FaceDetector(cfg.detect.score_threshold), PassiveLiveness()
    if args.aim > 0:
        aim(cfg, det, args.aim)

    scores: list[float] = []
    for run in range(args.runs):
        with Camera(cfg.camera) as cam:
            exp = expose_for_face(cam, det, cfg.camera) if cfg.camera.face_exposure else None
            rows = []
            for _ in range(args.frames):
                frame = cam.read()
                faces = det.detect(frame) if frame is not None else []
                if not faces:
                    rows.append("--")
                    continue
                face = max(faces, key=lambda f: f.box[2])
                s = live.score(frame, face)
                scores.append(s)
                rows.append(f"{s:.2f}")
        how = "auto" if exp is None or exp.exposure is None else f"manual {exp.exposure:+.0f}"
        fb = "" if exp is None or exp.final is None else f" face_brightness={exp.final:.0f}"
        print(f"run {run + 1} ({how}{fb}): {' '.join(rows)}")
        time.sleep(1.0)

    if not scores:
        print(f"[{args.label}] no faces scored")
        return 1
    s = sorted(scores)
    print(f"[{args.label}] n={len(s)} min={s[0]:.2f} p10={s[len(s) // 10]:.2f} "
          f"median={statistics.median(s):.2f} max={s[-1]:.2f} "
          f">= {cfg.liveness.passive_threshold}: {sum(x >= cfg.liveness.passive_threshold for x in s)}/{len(s)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
