"""The camera is released after the reply, never leaked, and released at once on errors."""
from __future__ import annotations

import pytest

from face_unlock import detection, liveness, service, verifier
from face_unlock.camera import CameraError
from face_unlock.config import Config
from face_unlock.verifier import Reason, VerifyResult


class FakeCamera:
    events: list[str]

    def __init__(self, cfg: object) -> None:
        pass

    def __enter__(self) -> FakeCamera:
        self.events.append("open")
        return self

    def __exit__(self, *exc: object) -> None:
        self.events.append("release")

    def read(self) -> None:
        return None  # no face: face-priority exposure leaves the camera alone


class FakeVerifier:
    outcome: VerifyResult | Exception = VerifyResult(True, Reason.NONE)

    def __init__(self, cfg: object, *models: object, **kw: object) -> None:
        pass

    def verify(self, cam: object, gallery: object, direction: object) -> VerifyResult:
        FakeCamera.events.append("verify")
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


@pytest.fixture
def events(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    FakeCamera.events = []
    monkeypatch.setattr(service, "Camera", FakeCamera)
    monkeypatch.setattr(service.stores, "load_gallery", lambda dev=False: object())
    monkeypatch.setattr(detection, "FaceDetector", lambda *a: object())
    monkeypatch.setattr(detection, "FaceEmbedder", lambda *a: object())
    monkeypatch.setattr(liveness, "PassiveLiveness", lambda *a: object())
    monkeypatch.setattr(verifier, "Verifier", FakeVerifier)
    FakeVerifier.outcome = VerifyResult(True, Reason.NONE)
    return FakeCamera.events


def test_release_waits_for_after_reply(events: list[str]) -> None:
    after = service.AfterReply()
    result = service.make_verify_fn(Config(), after)(None)
    assert result.ok
    assert events == ["open", "verify"]  # still open: the reply goes out first
    after.run()
    assert events == ["open", "verify", "release"]
    after.run()  # idempotent: nothing left to release
    assert events.count("release") == 1


def test_failed_verify_also_defers(events: list[str]) -> None:
    FakeVerifier.outcome = VerifyResult(False, Reason.NO_MATCH)
    after = service.AfterReply()
    assert service.make_verify_fn(Config(), after)(None).reason is Reason.NO_MATCH
    after.run()
    assert events == ["open", "verify", "release"]


def test_exception_releases_immediately(events: list[str]) -> None:
    FakeVerifier.outcome = RuntimeError("boom")
    after = service.AfterReply()
    with pytest.raises(RuntimeError):
        service.make_verify_fn(Config(), after)(None)
    assert events == ["open", "verify", "release"]
    after.run()
    assert events.count("release") == 1


def test_camera_error_is_reported(events: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    class Broken(FakeCamera):
        def __enter__(self) -> FakeCamera:
            raise CameraError("in use")

    monkeypatch.setattr(service, "Camera", Broken)
    after = service.AfterReply()
    assert service.make_verify_fn(Config(), after)(None).reason is Reason.CAMERA_ERROR
    after.run()
    assert events == []


def test_after_reply_runs_everything_despite_failures() -> None:
    ran: list[int] = []
    after = service.AfterReply()

    def bad() -> None:
        raise OSError("driver hiccup")

    after.add(bad)
    after.add(lambda: ran.append(1))
    after.run()
    assert ran == [1]
