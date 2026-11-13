"""YuNet face detection and SFace embeddings via OpenCV.

Both work on BGR numpy frames, so they are testable with images on disk.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from numpy.typing import NDArray

from face_unlock.models import SFACE, YUNET, model_path

Frame = NDArray[np.uint8]  # HxWx3 BGR


@dataclass(frozen=True)
class Face:
    """One YuNet detection. `raw` is the 15-float row SFace's alignCrop expects."""

    raw: NDArray[np.float32]

    @property
    def box(self) -> tuple[float, float, float, float]:
        x, y, w, h = self.raw[:4]
        return float(x), float(y), float(w), float(h)

    @property
    def landmarks(self) -> NDArray[np.float32]:
        """5x2: right eye, left eye, nose tip, right mouth corner, left mouth corner (image coords)."""
        return self.raw[4:14].reshape(5, 2)

    @property
    def score(self) -> float:
        return float(self.raw[14])


class FaceDetector:
    def __init__(self, score_threshold: float = 0.9, models: Path | None = None) -> None:
        self._net = cv2.FaceDetectorYN.create(
            str(model_path(YUNET, models)), "", (320, 320), score_threshold, 0.3, 5000
        )
        self._size: tuple[int, int] | None = None

    def detect(self, frame: Frame) -> list[Face]:
        h, w = frame.shape[:2]
        if self._size != (w, h):
            self._net.setInputSize((w, h))
            self._size = (w, h)
        _, faces = self._net.detect(frame)
        if faces is None:
            return []
        return [Face(row.astype(np.float32)) for row in faces]


class FaceEmbedder:
    def __init__(self, models: Path | None = None) -> None:
        self._net = cv2.FaceRecognizerSF.create(str(model_path(SFACE, models)), "")

    def embed(self, frame: Frame, face: Face) -> NDArray[np.float32]:
        aligned = self._net.alignCrop(frame, face.raw)
        return self._net.feature(aligned).reshape(-1).astype(np.float32)


def rival_faces(faces: list[Face], rival_ratio: float) -> list[Face]:
    """Faces at least `rival_ratio` x the width of the largest one, largest first.

    Used by verify: a photo or person right next to you is about your size and
    counts; people farther back in a busy room are much smaller and don't.
    """
    if not faces:
        return []
    ordered = sorted(faces, key=lambda f: f.box[2], reverse=True)
    biggest = ordered[0].box[2]
    return [f for f in ordered if f.box[2] >= rival_ratio * biggest]


def single_face(faces: list[Face], min_face_px: int) -> Face | None:
    """Return the face only if exactly one is present and it is big enough.

    More than one face is rejected outright: we can't tell which one the user
    is, and a second person holding a photo next to you is a known attack.
    """
    if len(faces) != 1:
        return None
    face = faces[0]
    return face if face.box[2] >= min_face_px else None
