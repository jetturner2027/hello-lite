"""Test doubles: faces and frames without a camera or models."""
from __future__ import annotations

import numpy as np

from face_unlock.detection import Face
from face_unlock.matching import EMBEDDING_DIM

FRAME = np.zeros((480, 640, 3), np.uint8)


def make_face(x: float = 270, y: float = 190, w: float = 100, yaw: float = 0.0, score: float = 0.99) -> Face:
    """A face whose nose is offset by `yaw` x eye distance from the eye midpoint."""
    eye_y, eye_dist = y + 0.4 * w, 0.4 * w
    cx = x + w / 2
    re, le = (cx - eye_dist / 2, eye_y), (cx + eye_dist / 2, eye_y)
    nose = (cx + yaw * eye_dist, y + 0.6 * w)
    mouth_r, mouth_l = (cx - 0.15 * w, y + 0.8 * w), (cx + 0.15 * w, y + 0.8 * w)
    raw = [x, y, w, w, *re, *le, *nose, *mouth_r, *mouth_l, score]
    return Face(np.array(raw, np.float32))


def identity(seed: int) -> np.ndarray:
    return np.random.default_rng(seed).standard_normal(EMBEDDING_DIM).astype(np.float32)


class ScriptedSource:
    """Yields one frame per scripted step; the detector reads the same script."""

    def __init__(self, steps: list[dict]) -> None:
        self.steps = steps
        self.i = -1

    def read(self) -> np.ndarray | None:
        self.i += 1
        if self.i >= len(self.steps) or self.steps[self.i].get("camera_fail"):
            return None
        return FRAME


class ScriptedDetector:
    def __init__(self, src: ScriptedSource) -> None:
        self.src = src

    def detect(self, frame: np.ndarray) -> list[Face]:
        return self.src.steps[self.src.i].get("faces", [])


class ScriptedEmbedder:
    def __init__(self, src: ScriptedSource) -> None:
        self.src = src

    def embed(self, frame: np.ndarray, face: Face) -> np.ndarray:
        return self.src.steps[self.src.i]["emb"]


class ScriptedLiveness:
    def __init__(self, src: ScriptedSource) -> None:
        self.src = src

    def score(self, frame: np.ndarray, face: Face) -> float:
        return self.src.steps[self.src.i].get("live", 0.99)


class FakeClock:
    def __init__(self, step: float = 0.05) -> None:
        self.t = 0.0
        self.step = step

    def __call__(self) -> float:
        self.t += self.step
        return self.t
