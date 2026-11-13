"""Pinned model files. Every model is hash-verified before it is loaded.

YuNet and SFace hashes come from the Git LFS pointers in opencv_zoo at the
pinned commit: an independent record, not just "whatever we downloaded".
The MiniFASNet ONNX files are converted locally from minivision's official
weights by tools/convert_minifasnet.py; their hashes are pinned after
conversion (see that script for the source .pth hashes).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from face_unlock.integrity import verify_file

OPENCV_ZOO_COMMIT = "48b10fa505aeb51fbd127fc60a5c660040e823b7"
_ZOO = f"https://media.githubusercontent.com/media/opencv/opencv_zoo/{OPENCV_ZOO_COMMIT}/models"


@dataclass(frozen=True)
class ModelSpec:
    filename: str
    sha256: str
    url: str | None  # None = produced locally, not downloaded


YUNET = ModelSpec(
    "face_detection_yunet_2023mar.onnx",
    "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
    f"{_ZOO}/face_detection_yunet/face_detection_yunet_2023mar.onnx",
)
SFACE = ModelSpec(
    "face_recognition_sface_2021dec.onnx",
    "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79",
    f"{_ZOO}/face_recognition_sface/face_recognition_sface_2021dec.onnx",
)
# From tools/convert_minifasnet.py (torch 2.5.1, onnx 1.17.0, opset 13) on 2026-11-09.
# Re-converting with other versions can change the bytes; re-pin if you do.
MINIFAS_V2 = ModelSpec(
    "minifasnet_v2_2.7_80x80.onnx", "602be1888e24ad060d18eec7226fa7065b489b5b970d549c3a947cc10314563a", None
)
MINIFAS_V1SE = ModelSpec(
    "minifasnet_v1se_4.0_80x80.onnx", "a5d4e5fe8d5ba461ceea91b6b77869bdc61e5aa807d4449e8f0d9f880b8d3b45", None
)

ALL_MODELS = (YUNET, SFACE, MINIFAS_V2, MINIFAS_V1SE)


def models_dir() -> Path:
    """FACEUNLOCK_MODELS overrides; then <repo>/models in a checkout; else the install dir."""
    if env := os.environ.get("FACEUNLOCK_MODELS"):
        return Path(env)
    repo = Path(__file__).resolve().parent.parent / "models"
    if repo.is_dir():
        return repo
    return Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "FaceUnlock" / "models"


def model_path(spec: ModelSpec, directory: Path | None = None) -> Path:
    """Return the verified path to a model, raising IntegrityError on mismatch."""
    return verify_file((directory or models_dir()) / spec.filename, spec.sha256)
