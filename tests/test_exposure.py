"""Face-priority exposure, against a simulated camera whose face brightness tracks exposure."""
from __future__ import annotations

import numpy as np

from face_unlock.config import CameraConfig
from face_unlock.detection import Face
from face_unlock.exposure import expose_for_face, face_brightness
from tests.fakes import make_face

FACE = make_face(x=270, y=190, w=100)
CFG = CameraConfig(dark_face_brightness=80.0, target_face_brightness=110.0)  # start -6, max -3, settle 3


class SimCamera:
    """Face brightness for each exposure value; auto exposure gives `auto_face`.

    Like the real driver, a change only shows up after `lag` buffered frames.
    `lost_at` = exposures where the face is too blown out to detect.
    """

    def __init__(self, auto_face: float, by_exposure: dict[float, float], lost_at: tuple[float, ...] = (),
                 accept: bool = True, lag: int = 3) -> None:
        self.auto_face, self.by_exposure, self.lost_at = auto_face, by_exposure, lost_at
        self.accept, self.lag = accept, lag
        self.mode: float | None = None  # None = auto
        self.pending: list[float | None] = []
        self.history: list[float | None] = []

    def set_exposure(self, value: float) -> bool:
        self.history.append(value)
        if self.accept:
            self.pending = [self.mode] * self.lag
            self.mode = value
        return self.accept

    def auto_exposure(self) -> None:
        self.history.append(None)
        self.pending = [self.mode] * self.lag
        self.mode = None

    def read(self) -> np.ndarray:
        shown = self.pending.pop(0) if self.pending else self.mode
        level = self.auto_face if shown is None else self.by_exposure[shown]
        frame = np.zeros((480, 640, 3), np.uint8)
        if shown not in self.lost_at:
            frame[190:290, 270:370] = int(level)
        frame[0, 0] = 1 if shown in self.lost_at else 0  # marker for the detector
        return frame


class SimDetector:
    def detect(self, frame: np.ndarray) -> list[Face]:
        return [] if frame[0, 0, 0] == 1 else [FACE]


LADDER = {-6.0: 60.0, -5.0: 95.0, -4.0: 130.0, -3.0: 170.0}


def test_face_brightness_is_inside_box_only() -> None:
    frame = np.full((480, 640, 3), 250, np.uint8)
    frame[190:290, 270:370] = 50
    assert face_brightness(frame, FACE) == 50.0


def test_normal_light_changes_nothing() -> None:
    cam = SimCamera(auto_face=130, by_exposure=LADDER)
    r = expose_for_face(cam, SimDetector(), CFG)
    assert r.exposure is None and cam.history == []


def test_no_face_changes_nothing() -> None:
    class NoFace(SimDetector):
        def detect(self, frame: np.ndarray) -> list[Face]:
            return []

    cam = SimCamera(auto_face=30, by_exposure=LADDER)
    assert expose_for_face(cam, NoFace(), CFG).exposure is None and cam.history == []


def test_backlit_steps_up_until_target() -> None:
    cam = SimCamera(auto_face=40, by_exposure=LADDER)
    r = expose_for_face(cam, SimDetector(), CFG)
    assert cam.history == [-6.0, -5.0, -4.0]  # -5 gives 95 < 110, so one more step
    assert r.exposure == -4.0 and r.final == 130.0 and r.initial == 40.0
    assert cam.mode == -4.0


def test_measures_after_the_change_not_buffered_frames() -> None:
    # If buffered frames were measured, -6 would look like auto (40) and -5 would be skipped wrongly.
    cam = SimCamera(auto_face=40, by_exposure={-6.0: 120.0, -5.0: 200.0, -4.0: 220.0, -3.0: 240.0})
    assert expose_for_face(cam, SimDetector(), CFG).exposure == -6.0


def test_stops_at_max_exposure() -> None:
    cam = SimCamera(auto_face=10, by_exposure={-6.0: 20.0, -5.0: 30.0, -4.0: 40.0, -3.0: 50.0, -2.0: 200.0})
    r = expose_for_face(cam, SimDetector(), CFG)
    assert max(v for v in cam.history if v is not None) == -3.0
    assert r.exposure == -3.0 and cam.mode == -3.0


def test_face_lost_goes_back_one_step() -> None:
    cam = SimCamera(auto_face=40, by_exposure=LADDER | {-5.0: 100.0}, lost_at=(-4.0,))
    r = expose_for_face(cam, SimDetector(), CFG)
    assert r.exposure == -5.0 and cam.mode == -5.0


def test_lost_on_first_step_returns_to_auto() -> None:
    cam = SimCamera(auto_face=40, by_exposure=LADDER, lost_at=(-6.0,))
    r = expose_for_face(cam, SimDetector(), CFG)
    assert r.exposure is None and cam.mode is None and cam.history[-1] is None


def test_driver_refuses_manual_exposure() -> None:
    cam = SimCamera(auto_face=40, by_exposure=LADDER, accept=False)
    r = expose_for_face(cam, SimDetector(), CFG)
    assert r.exposure is None and cam.mode is None
