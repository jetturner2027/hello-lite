"""Webcam capture. Opened only for the duration of a `with` block."""
from __future__ import annotations

from types import TracebackType
from typing import Protocol

import cv2

from face_unlock.config import CameraConfig
from face_unlock.detection import Frame


# DirectShow: 0.75 (or 1) switches the driver back to auto exposure; verified on the
# IdeaPad webcam (manual -8 -> brightness 14, then 0.75 -> 110). 3 does not.
AUTO_EXPOSURE_ON = 0.75


class CameraError(Exception):
    pass


class FrameSource(Protocol):
    def read(self) -> Frame | None: ...


class Camera:
    """DirectShow capture. MSMF (OpenCV's default on Windows) is slower to open
    on many laptop webcams, and open latency is most of our unlock budget."""

    def __init__(self, cfg: CameraConfig) -> None:
        self._cfg = cfg
        self._cap: cv2.VideoCapture | None = None
        self._manual_exposure = False

    def __enter__(self) -> Camera:
        cap = cv2.VideoCapture(self._cfg.index, cv2.CAP_DSHOW)
        if not cap.isOpened():
            cap.release()
            raise CameraError(f"cannot open camera {self._cfg.index} (in use or absent)")
        # With DirectShow each size change can restart the stream (~0.65 s measured on the
        # IdeaPad), so only ask when the driver's default isn't already what we want.
        if (cap.get(cv2.CAP_PROP_FRAME_WIDTH), cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) != (
            self._cfg.width,
            self._cfg.height,
        ):
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self._cfg.width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self._cfg.height)
        # Auto exposure at every open (11 ms): if a previous process died while the camera
        # was on manual exposure (exposure.py), the driver would otherwise keep it.
        cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, AUTO_EXPOSURE_ON)
        self._cap = cap
        self._manual_exposure = False
        for _ in range(self._cfg.warmup_frames):
            cap.read()
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        if self._cap is not None:
            if self._manual_exposure:
                self.auto_exposure()  # don't leave Teams/Camera app on our manual setting
            self._cap.release()
            self._cap = None

    def set_exposure(self, value: float) -> bool:
        """Manual exposure in driver units (log2 seconds). Undone by auto_exposure() or close."""
        if self._cap is None:
            raise CameraError("camera not open")
        self._manual_exposure = True  # even if the driver says no, restore auto on close
        return bool(self._cap.set(cv2.CAP_PROP_EXPOSURE, value))

    def auto_exposure(self) -> None:
        if self._cap is None:
            raise CameraError("camera not open")
        self._cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, AUTO_EXPOSURE_ON)
        self._manual_exposure = False

    def read(self) -> Frame | None:
        if self._cap is None:
            raise CameraError("camera not open")
        ok, frame = self._cap.read()
        return frame if ok else None
