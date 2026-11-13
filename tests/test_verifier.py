from dataclasses import replace

import numpy as np

from face_unlock.config import Config
from face_unlock.liveness import Direction
from face_unlock.matching import Gallery
from face_unlock.verifier import Reason, Verifier
from tests.fakes import (
    FakeClock, ScriptedDetector, ScriptedEmbedder, ScriptedLiveness, ScriptedSource, identity, make_face,
)

ME, OTHER = identity(1), identity(2)
GALLERY = Gallery(np.stack([ME + 0.05 * identity(10 + i) for i in range(10)]))
# Most tests exercise the full flow, so they opt in to the (default-off) challenge.
CHALLENGE = replace(Config(), liveness=replace(Config().liveness, challenge=True))
NO_CHALLENGE = Config()


def run(steps: list[dict], direction: Direction | None = Direction.LEFT, cfg: Config = CHALLENGE):
    src = ScriptedSource(steps)
    v = Verifier(cfg, ScriptedDetector(src), ScriptedEmbedder(src), ScriptedLiveness(src), FakeClock())
    return v.verify(src, GALLERY, direction)


def me(yaw: float = 0.0, **kw) -> dict:
    return {"faces": [make_face(yaw=yaw)], "emb": ME, **kw}


TURN_LEFT = [me(0.5), me(0.3), me(0.0)]


def test_happy_path() -> None:
    r = run([me()] * 3 + TURN_LEFT)
    assert r.ok and r.reason is Reason.NONE


def test_challenge_off_is_look_and_go() -> None:
    r = run([me()] * 3, direction=None, cfg=NO_CHALLENGE)
    assert r.ok and r.frames == 3


def test_challenge_off_ignores_a_direction_if_sent() -> None:
    assert run([me()] * 3, direction=Direction.LEFT, cfg=NO_CHALLENGE).ok


def test_challenge_on_but_no_direction_fails_closed() -> None:
    r = run([me()] * 3, direction=None, cfg=CHALLENGE)
    assert not r.ok and r.reason is Reason.BAD_REQUEST and r.frames == 0  # camera never read


def test_stranger_rejected() -> None:
    other = {"faces": [make_face()], "emb": OTHER}
    r = run([other] * 5)
    assert not r.ok and r.reason is Reason.NO_MATCH


def test_spoof_rejected() -> None:
    r = run([me(live=0.1)] * 5)
    assert not r.ok and r.reason is Reason.SPOOF


def test_vote_tolerates_noise() -> None:
    noisy = [me(), me(live=0.1), me(), me()]
    assert run(noisy + TURN_LEFT).ok


def test_multiple_faces_rejected_immediately() -> None:
    r = run([{"faces": [make_face(), make_face(x=50)], "emb": ME}])
    assert r.reason is Reason.MULTIPLE_FACES


def test_similar_size_second_face_rejected() -> None:
    beside = make_face(x=420, w=70)  # 70% of the user's 100 px: a photo/person at your shoulder
    r = run([{"faces": [make_face(), beside], "emb": ME}])
    assert r.reason is Reason.MULTIPLE_FACES


def test_small_background_faces_ignored() -> None:
    crowd = [make_face(x=20, y=20, w=40), make_face(x=560, y=30, w=35)]  # people across the room
    r = run([{"faces": [make_face(), *crowd], "emb": ME}] * 3 + [
        {"faces": [make_face(yaw=0.5), *crowd], "emb": ME},
        {"faces": [make_face(yaw=0.0), *crowd], "emb": ME},
    ])
    assert r.ok, r.reason


def test_background_face_cannot_stand_in_when_user_leaves() -> None:
    # Mid-challenge the user's face vanishes and only a small background face remains:
    # it becomes "largest", and its center jump fails the challenge.
    bg = make_face(x=20, y=20, w=40)
    r = run([me()] * 3 + [{"faces": [bg], "emb": ME}])
    assert r.reason is Reason.CHALLENGE_FAILED


def test_challenge_wrong_direction_times_out() -> None:
    turn_right = [me(-0.5), me(0.0)] * 200
    r = run([me()] * 3 + turn_right)
    assert not r.ok and r.reason is Reason.CHALLENGE_FAILED


def test_face_swap_during_challenge_rejected() -> None:
    swapped = {"faces": [make_face(x=20, yaw=0.5)], "emb": ME}  # jumped across the frame
    r = run([me()] * 3 + [swapped, me(0.0)])
    assert r.reason is Reason.CHALLENGE_FAILED


def test_stranger_returning_after_turn_rejected() -> None:
    back_as_other = {"faces": [make_face(yaw=0.0)], "emb": OTHER}
    r = run([me()] * 3 + [me(0.5), back_as_other])
    assert r.reason is Reason.NO_MATCH


def test_camera_failure() -> None:
    assert run([{"camera_fail": True}]).reason is Reason.CAMERA_ERROR


def test_timeout_with_nobody_in_frame() -> None:
    cfg = replace(CHALLENGE, verify=replace(CHALLENGE.verify, timeout_s=1.0))
    r = run([{"faces": []}] * 100, cfg=cfg)
    assert r.reason is Reason.NO_FACE


def test_cancel_stops_at_next_frame() -> None:
    # Camera covered, then the PIN is typed: the verify must stop, not run to the timeout.
    src = ScriptedSource([{"faces": []}] * 100)
    reads = {"n": 0}
    real_read = src.read

    def counting_read():
        reads["n"] += 1
        return real_read()

    src.read = counting_read  # type: ignore[method-assign]
    v = Verifier(NO_CHALLENGE, ScriptedDetector(src), ScriptedEmbedder(src), ScriptedLiveness(src), FakeClock(),
                 cancelled=lambda: reads["n"] >= 3)
    r = v.verify(src, GALLERY, None)
    assert r.reason is Reason.NOT_LOCKED and reads["n"] == 3


def test_cancel_before_start_never_reads() -> None:
    src = ScriptedSource([me()] * 3)
    v = Verifier(NO_CHALLENGE, ScriptedDetector(src), ScriptedEmbedder(src), ScriptedLiveness(src), FakeClock(),
                 cancelled=lambda: True)
    r = v.verify(src, GALLERY, None)
    assert not r.ok and r.reason is Reason.NOT_LOCKED and r.frames == 0


def test_tiny_face_ignored() -> None:
    tiny = {"faces": [make_face(w=30)], "emb": ME}
    cfg = replace(CHALLENGE, verify=replace(CHALLENGE.verify, timeout_s=1.0))
    assert run([tiny] * 100, cfg=cfg).reason is Reason.NO_FACE
