"""Face-priority exposure for backlit scenes (a window behind the user).

The webcam's auto exposure meters the whole frame, so a bright window makes it
pick a short exposure and the face comes out dark and flat. MiniFASNet scores
such faces as "not real" (measured 2026-11-10: 0.55-0.70 backlit vs ~1.00 in
normal light). Brightening the image in software would feed the model input
unlike its training data, so instead we lengthen the real exposure until the
face itself is properly exposed. The window blows out, which doesn't matter.

Only kicks in when the face is dark; in normal light it reads one frame and
changes nothing. Camera restores auto exposure on close.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import cv2
import numpy as np

from face_unlock.config import CameraConfig
from face_unlock.detection import Face, Frame


class ExposureCamera(Protocol):
    def read(self) -> Frame | None: ...
    def set_exposure(self, value: float) -> bool: ...
    def auto_exposure(self) -> None: ...


class Detector(Protocol):
    def detect(self, frame: Frame) -> list[Face]: ...


def face_brightness(frame: Frame, face: Face) -> float:
    """Mean grey level inside the face box (clipped to the frame)."""
    h, w = frame.shape[:2]
    x, y, bw, bh = face.box
    x1, y1 = max(0, int(x)), max(0, int(y))
    x2, y2 = min(w, int(x + bw)), min(h, int(y + bh))
    if x2 <= x1 or y2 <= y1:
        return 0.0
    patch = frame[y1:y2, x1:x2]
    grey = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY) if patch.ndim == 3 else patch
    return float(np.mean(grey))


@dataclass(frozen=True)
class ExposureResult:
    initial: float | None  # face brightness on auto exposure; None if no face was seen
    final: float | None
    exposure: float | None  # manual value chosen; None = auto exposure kept


def _largest_face_brightness(cam: ExposureCamera, detector: Detector, settle: int) -> float | None:
    frame = None
    for _ in range(settle + 1):  # the camera buffers a few frames taken before the change
        frame = cam.read()
    if frame is None:
        return None
    faces = detector.detect(frame)
    if not faces:
        return None
    return face_brightness(frame, max(faces, key=lambda f: f.box[2]))


def expose_for_face(cam: ExposureCamera, detector: Detector, cfg: CameraConfig) -> ExposureResult:
    """Lengthen exposure step by step until the face reaches the target brightness.

    Stops at cfg.exposure_max (longer exposures blur and drop the frame rate).
    If the face disappears at a step (badly overexposed or moved away), goes back
    to the last step where it was seen.
    """
    initial = _largest_face_brightness(cam, detector, settle=0)
    if initial is None or initial >= cfg.dark_face_brightness:
        return ExposureResult(initial, initial, None)

    best: tuple[float, float] | None = None  # (exposure, brightness) of the last step with a face
    applied: float | None = None
    value = cfg.exposure_start
    while value <= cfg.exposure_max:
        if not cam.set_exposure(value):
            break
        applied = value
        b = _largest_face_brightness(cam, detector, cfg.exposure_settle_frames)
        if b is None:
            break
        best = (value, b)
        if b >= cfg.target_face_brightness:
            break
        value += 1.0

    if best is None:
        # Nothing usable in manual mode: hand back to auto and let verify try as usual.
        cam.auto_exposure()
        _largest_face_brightness(cam, detector, cfg.exposure_settle_frames)
        return ExposureResult(initial, None, None)
    if applied != best[0]:
        cam.set_exposure(best[0])
        _largest_face_brightness(cam, detector, cfg.exposure_settle_frames)
    return ExposureResult(initial, best[1], best[0])
