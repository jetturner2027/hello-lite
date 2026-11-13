"""Liveness: passive MiniFASNet scoring and an active head-turn challenge.

Passive: minivision's two MiniFASNet models (Silent-Face-Anti-Spoofing),
converted to ONNX. Preprocessing matches upstream exactly: crop an enlarged
box around the face, resize to 80x80, BGR, raw 0-255 floats (upstream's
to_tensor deliberately skips the /255), NCHW. Softmax over 3 classes; class 1
is "real". Upstream averages the two models' softmax outputs.

Active: YuNet only gives 5 landmarks (eye centers, nose, mouth corners), so
blink detection isn't possible without another model. Head turn is: the nose's
horizontal offset from the eye midpoint, divided by eye distance, is a cheap
yaw proxy that a flat photo can only fake by being physically rotated.
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import cv2
import numpy as np
from numpy.typing import NDArray

from face_unlock.detection import Face, Frame
from face_unlock.models import MINIFAS_V1SE, MINIFAS_V2, ModelSpec, model_path


def crop_box(
    src_w: int, src_h: int, box: tuple[float, float, float, float], scale: float
) -> tuple[int, int, int, int]:
    """Port of upstream CropImage._get_new_box: enlarge around center, shift inside image.

    Returns inclusive (x1, y1, x2, y2).
    """
    x, y, bw, bh = box
    scale = min((src_h - 1) / bh, (src_w - 1) / bw, scale)
    nw, nh = bw * scale, bh * scale
    cx, cy = x + bw / 2, y + bh / 2
    x1, y1, x2, y2 = cx - nw / 2, cy - nh / 2, cx + nw / 2, cy + nh / 2
    if x1 < 0:
        x2 -= x1
        x1 = 0
    if y1 < 0:
        y2 -= y1
        y1 = 0
    if x2 > src_w - 1:
        x1 -= x2 - src_w + 1
        x2 = src_w - 1
    if y2 > src_h - 1:
        y1 -= y2 - src_h + 1
        y2 = src_h - 1
    return int(x1), int(y1), int(x2), int(y2)


def preprocess(frame: Frame, face: Face, scale: float, size: int = 80) -> NDArray[np.float32]:
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = crop_box(w, h, face.box, scale)
    patch = cv2.resize(frame[y1 : y2 + 1, x1 : x2 + 1], (size, size))
    return patch.transpose(2, 0, 1)[np.newaxis].astype(np.float32)


def _softmax(logits: NDArray[np.float32]) -> NDArray[np.float32]:
    e = np.exp(logits - logits.max(axis=-1, keepdims=True))
    return e / e.sum(axis=-1, keepdims=True)


class PassiveLiveness:
    _MODELS: tuple[tuple[ModelSpec, float], ...] = ((MINIFAS_V2, 2.7), (MINIFAS_V1SE, 4.0))

    def __init__(self, models: Path | None = None) -> None:
        import onnxruntime as ort  # imported lazily: only the verify path needs it

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 2  # i3 has 2 physical cores
        self._sessions = [
            (ort.InferenceSession(str(model_path(spec, models)), opts, providers=["CPUExecutionProvider"]), scale)
            for spec, scale in self._MODELS
        ]

    def score(self, frame: Frame, face: Face) -> float:
        """Probability-like score in [0, 1] that the face is real."""
        probs = np.zeros(3, dtype=np.float32)
        for session, scale in self._sessions:
            inp = session.get_inputs()[0].name
            out = session.run(None, {inp: preprocess(frame, face, scale)})[0].reshape(-1)
            probs += _softmax(out)
        return float(probs[1] / len(self._sessions))


def yaw_ratio(face: Face) -> float:
    """Nose offset from the eye midpoint, in units of inter-eye distance.

    ~0 frontal. Sign follows image x: positive = nose right of eye midpoint in
    the (unmirrored) frame, i.e. the user turned to *their* left.
    """
    lm = face.landmarks
    right_eye, left_eye, nose = lm[0], lm[1], lm[2]
    eye_dist = float(np.linalg.norm(left_eye - right_eye))
    if eye_dist < 1e-3:
        return 0.0
    mid_x = (right_eye[0] + left_eye[0]) / 2
    return float((nose[0] - mid_x) / eye_dist)


class Direction(Enum):
    # Named from the user's perspective, as shown in the prompt.
    LEFT = 1  # user's left => nose moves to +x in the raw (unmirrored) frame
    RIGHT = -1


def random_direction() -> Direction:
    return secrets.choice(list(Direction))


@dataclass
class HeadTurnChallenge:
    """Pass when the user turns in the requested direction and comes back frontal.

    Requiring the return to frontal means a single pre-rotated photo can't pass;
    the attacker has to produce motion in a direction chosen at random.
    """

    direction: Direction
    turn_ratio: float = 0.25
    frontal_ratio: float = 0.12
    _turned: bool = False
    _done: bool = False

    def update(self, face: Face) -> bool:
        r = yaw_ratio(face) * self.direction.value
        if not self._turned:
            # A turn the wrong way is ignored, not failed; the caller enforces the time limit.
            self._turned = r >= self.turn_ratio
        elif abs(r) <= self.frontal_ratio:
            self._done = True
        return self._done

    @property
    def turned(self) -> bool:
        return self._turned

    @property
    def passed(self) -> bool:
        return self._done
