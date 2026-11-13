"""The verify flow: N-of-M frames of (match AND passive liveness), then a head-turn challenge.

All dependencies are injected so the logic is testable with fake frames.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Protocol

import numpy as np
from numpy.typing import NDArray

from face_unlock.camera import FrameSource
from face_unlock.config import Config
from face_unlock.detection import Face, Frame, rival_faces, single_face
from face_unlock.liveness import Direction, HeadTurnChallenge
from face_unlock.matching import Gallery
from face_unlock.voting import FrameVote, Verdict


class Reason(IntEnum):
    """Wire values are part of the pipe protocol; append only."""

    NONE = 0
    NO_FACE = 1
    MULTIPLE_FACES = 2
    NO_MATCH = 3
    SPOOF = 4
    CHALLENGE_FAILED = 5
    TIMEOUT = 6
    CAMERA_ERROR = 7
    NOT_ENROLLED = 8
    RATE_LIMITED = 9
    BAD_REQUEST = 10
    INTERNAL = 11
    NOT_LOCKED = 12


class Detector(Protocol):
    def detect(self, frame: Frame) -> list[Face]: ...


class Embedder(Protocol):
    def embed(self, frame: Frame, face: Face) -> NDArray[np.float32]: ...


class Liveness(Protocol):
    def score(self, frame: Frame, face: Face) -> float: ...


@dataclass
class VerifyResult:
    ok: bool
    reason: Reason
    frames: int = 0
    timings_ms: dict[str, float] = field(default_factory=dict)


MAX_MISSED_DURING_TURN = 4  # YuNet can drop a strongly turned face for a frame or two
MAX_JUMP_FACE_WIDTHS = 1.0  # center movement per frame beyond this = a different face


class Verifier:
    def __init__(
        self,
        cfg: Config,
        detector: Detector,
        embedder: Embedder,
        liveness: Liveness,
        clock: Callable[[], float] = time.monotonic,
        cancelled: Callable[[], bool] = lambda: False,
    ) -> None:
        self._cfg = cfg
        self._det = detector
        self._emb = embedder
        self._live = liveness
        self._clock = clock
        # Checked every frame: the session got unlocked another way (PIN typed during a
        # background check), so stop using the camera now instead of at the timeout.
        self._cancelled = cancelled

    def verify(self, source: FrameSource, gallery: Gallery, direction: Direction | None) -> VerifyResult:
        deadline = self._clock() + self._cfg.verify.timeout_s
        timings: dict[str, float] = {}
        frames = 0

        def fail(reason: Reason) -> VerifyResult:
            return VerifyResult(False, reason, frames, timings)

        if self._cfg.liveness.challenge and direction is None:
            # Fail closed: when the challenge is configured, a client can't opt out of it.
            return fail(Reason.BAD_REQUEST)

        # Phase A: frontal N-of-M voting on identity + passive liveness.
        vote = FrameVote(self._cfg.vote.required, self._cfg.vote.window)
        no_match = spoof = 0
        last_face: Face | None = None
        t0 = self._clock()
        while vote.verdict is Verdict.PENDING:
            if self._cancelled():
                return fail(Reason.NOT_LOCKED)
            if self._clock() > deadline:
                return fail(Reason.TIMEOUT if vote.seen else Reason.NO_FACE)
            frame = source.read()
            if frame is None:
                return fail(Reason.CAMERA_ERROR)
            frames += 1
            faces = rival_faces(self._det.detect(frame), self._cfg.detect.rival_ratio)
            if len(faces) > 1:
                return fail(Reason.MULTIPLE_FACES)
            face = single_face(faces, self._cfg.detect.min_face_px)
            if face is None:
                continue  # user not in frame yet; doesn't count as a vote
            matched = self._matches(frame, face, gallery)
            live = self._live.score(frame, face) >= self._cfg.liveness.passive_threshold
            no_match += not matched
            spoof += matched and not live
            vote.add(matched and live)
            last_face = face
        timings["vote"] = (self._clock() - t0) * 1000
        if vote.verdict is Verdict.FAIL:
            return fail(Reason.SPOOF if spoof > no_match else Reason.NO_MATCH)

        if not self._cfg.liveness.challenge:
            return VerifyResult(True, Reason.NONE, frames, timings)

        # Phase B: head turn in the requested direction, then back to frontal.
        challenge = HeadTurnChallenge(
            direction, self._cfg.liveness.turn_ratio, self._cfg.liveness.frontal_ratio
        )
        missed = 0
        t0 = self._clock()
        while True:
            if self._cancelled():
                return fail(Reason.NOT_LOCKED)
            if self._clock() > deadline:
                return fail(Reason.CHALLENGE_FAILED)
            frame = source.read()
            if frame is None:
                return fail(Reason.CAMERA_ERROR)
            frames += 1
            faces = rival_faces(self._det.detect(frame), self._cfg.detect.rival_ratio)
            if len(faces) > 1:
                return fail(Reason.MULTIPLE_FACES)
            if not faces:
                missed += 1
                if missed > MAX_MISSED_DURING_TURN:
                    return fail(Reason.CHALLENGE_FAILED)
                continue
            missed = 0
            face = faces[0]
            if last_face is not None and _jumped(last_face, face):
                return fail(Reason.CHALLENGE_FAILED)
            last_face = face
            if challenge.update(face):
                break
        # The face that came back to frontal must still be the enrolled user.
        if face.box[2] < self._cfg.detect.min_face_px or not self._matches(frame, face, gallery):
            return fail(Reason.NO_MATCH)
        timings["challenge"] = (self._clock() - t0) * 1000
        return VerifyResult(True, Reason.NONE, frames, timings)

    def _matches(self, frame: Frame, face: Face, gallery: Gallery) -> bool:
        emb = self._emb.embed(frame, face)
        return gallery.match(emb, self._cfg.match.threshold, self._cfg.match.top_k).matched


def _jumped(a: Face, b: Face) -> bool:
    ax, ay, aw, ah = a.box
    bx, by, bw, bh = b.box
    dist = float(np.hypot((ax + aw / 2) - (bx + bw / 2), (ay + ah / 2) - (by + bh / 2)))
    return dist > MAX_JUMP_FACE_WIDTHS * max(aw, bw)
