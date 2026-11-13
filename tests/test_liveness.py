import numpy as np
import pytest

from face_unlock.liveness import Direction, HeadTurnChallenge, crop_box, preprocess, yaw_ratio
from tests.fakes import FRAME, make_face


def test_crop_box_centered_scale() -> None:
    assert crop_box(640, 480, (300, 200, 40, 40), 2.0) == (280, 180, 360, 260)


def test_crop_box_scale_capped_to_image() -> None:
    x1, y1, x2, y2 = crop_box(640, 480, (100, 100, 200, 200), 4.0)
    assert (x1, y1) == (0, 0) and y2 == 479  # scale clamps to (h-1)/bh, then shifted inside


def test_crop_box_shifted_inside_left_edge() -> None:
    x1, _, x2, _ = crop_box(640, 480, (0, 200, 40, 40), 2.7)
    assert x1 == 0 and x2 == 108


def test_preprocess_shape_and_raw_range() -> None:
    frame = np.full((480, 640, 3), 200, np.uint8)
    t = preprocess(frame, make_face(), 2.7)
    assert t.shape == (1, 3, 80, 80) and t.dtype == np.float32
    assert t.max() == pytest.approx(200.0)  # upstream does NOT divide by 255


def test_yaw_ratio_sign() -> None:
    assert yaw_ratio(make_face(yaw=0.0)) == pytest.approx(0.0, abs=1e-6)
    assert yaw_ratio(make_face(yaw=0.5)) == pytest.approx(0.5, abs=1e-5)
    assert yaw_ratio(make_face(yaw=-0.5)) == pytest.approx(-0.5, abs=1e-5)


def test_challenge_requires_turn_then_return() -> None:
    c = HeadTurnChallenge(Direction.LEFT)
    assert not c.update(make_face(yaw=0.0))
    assert not c.update(make_face(yaw=0.5))  # turned (LEFT = +x)
    assert c.turned
    assert not c.update(make_face(yaw=0.3))  # not yet frontal
    assert c.update(make_face(yaw=0.05))


def test_challenge_wrong_direction_does_not_count() -> None:
    c = HeadTurnChallenge(Direction.RIGHT)
    c.update(make_face(yaw=0.5))  # that's LEFT
    assert not c.turned
    assert not c.update(make_face(yaw=0.0))


def test_static_turned_photo_cannot_pass() -> None:
    c = HeadTurnChallenge(Direction.LEFT)
    for _ in range(20):
        c.update(make_face(yaw=0.5))
    assert not c.passed
