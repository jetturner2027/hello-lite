import logging
from pathlib import Path

from face_unlock.protocol import MsgType, Request, Status, encode_request, parse_response
from face_unlock.ratelimit import FailureLimiter
from face_unlock.service_core import FACE_UNLOCK_ATTRIBUTION_S, FaceServiceCore
from face_unlock.verifier import Reason, VerifyResult
from tests.fakes import FakeClock

NONCE = bytes(range(1, 33))
SESSION = 1


# --- limiter ---------------------------------------------------------------

def test_disables_after_max_and_rearms_on_password(tmp_path: Path) -> None:
    lim = FailureLimiter(tmp_path / "s.json", 5)
    for _ in range(4):
        lim.record_failure()
    assert lim.allowed
    lim.record_failure()
    assert not lim.allowed
    lim.record_success()  # a success can't happen while disabled, but must not re-enable either
    assert not lim.allowed
    lim.password_logon()
    assert lim.allowed and lim.state.failures == 0


def test_success_resets_consecutive_count(tmp_path: Path) -> None:
    lim = FailureLimiter(None, 3)
    lim.record_failure(); lim.record_failure(); lim.record_success(); lim.record_failure(); lim.record_failure()
    assert lim.allowed


def test_state_survives_restart(tmp_path: Path) -> None:
    p = tmp_path / "s.json"
    lim = FailureLimiter(p, 2)
    lim.record_failure(); lim.record_failure()
    assert not FailureLimiter(p, 2).allowed


def test_corrupt_state_fails_closed(tmp_path: Path) -> None:
    p = tmp_path / "s.json"
    p.write_text("{not json")
    assert not FailureLimiter(p, 5).allowed


# --- service core ------------------------------------------------------------

class Harness:
    def __init__(self, results: list[VerifyResult], max_failures: int = 5, armed: bool = True) -> None:
        self.results = list(results)
        self.clock = FakeClock(step=0.01)
        self.limiter = FailureLimiter(None, max_failures)
        log = logging.getLogger("test.attempts")
        log.addHandler(logging.NullHandler())
        self.core = FaceServiceCore(self.limiter, lambda d: self.results.pop(0), log, self.clock)
        if armed:
            self.core.on_session_unlock(SESSION)  # you've unlocked once with your password since boot

    def send(self, mtype: MsgType = MsgType.VERIFY):
        raw = self.core.handle(encode_request(Request(mtype, NONCE)), SESSION)
        return parse_response(raw, NONCE, mtype)


OK = VerifyResult(True, Reason.NONE)
BAD = VerifyResult(False, Reason.NO_MATCH)


def test_refuses_when_not_locked() -> None:
    h = Harness([OK])
    r = h.send()
    assert r.status is Status.FAIL and r.reason is Reason.NOT_LOCKED
    assert h.results == [OK]  # camera never used


def test_verify_ok_when_locked() -> None:
    h = Harness([OK])
    h.core.on_session_lock(SESSION)
    assert h.send().status is Status.OK


def test_lockout_after_five_failures_and_rearm_by_password_unlock() -> None:
    h = Harness([BAD] * 5 + [OK])
    h.core.on_session_lock(SESSION)
    statuses = [h.send().status for _ in range(5)]
    assert statuses == [Status.FAIL] * 4 + [Status.DISABLED]
    r = h.send()
    assert r.status is Status.DISABLED and h.results == [OK]  # not even attempted
    h.core.on_session_unlock(SESSION)  # no recent face success => password/PIN unlock
    h.core.on_session_lock(SESSION)
    assert h.send().status is Status.OK


def test_face_unlock_does_not_count_as_password_logon() -> None:
    h = Harness([BAD, BAD, OK])
    h.core.on_session_lock(SESSION)
    h.send(); h.send(); h.send()
    h.limiter.state.failures = 4  # pretend we're close to the limit
    h.core.on_session_unlock(SESSION)  # right after the face success
    assert h.limiter.state.failures == 4


def test_old_face_success_is_not_attributed() -> None:
    h = Harness([OK])
    h.core.on_session_lock(SESSION)
    h.send()
    h.limiter.state.failures = 3
    h.clock.t += FACE_UNLOCK_ATTRIBUTION_S + 1
    h.core.on_session_unlock(SESSION)
    assert h.limiter.state.failures == 0


def test_environmental_failures_do_not_count() -> None:
    h = Harness([VerifyResult(False, Reason.CAMERA_ERROR)] * 10)
    h.core.on_session_lock(SESSION)
    for _ in range(10):
        h.send()
    assert h.limiter.allowed


def test_cancelled_verify_does_not_count() -> None:
    # PIN typed during a background check: the verify ends NOT_LOCKED, which isn't an attack.
    h = Harness([VerifyResult(False, Reason.NOT_LOCKED)] * 10)
    h.core.on_session_lock(SESSION)
    for _ in range(10):
        h.send()
    assert h.limiter.allowed


def test_report_logon_failed_disables() -> None:
    h = Harness([OK])
    h.core.on_session_lock(SESSION)
    h.send(MsgType.REPORT_LOGON_FAILED)
    assert h.send().status is Status.DISABLED


def test_verify_exception_is_answered_and_counted() -> None:
    h = Harness([])
    h.core.on_session_lock(SESSION)
    r = h.send()  # pop from empty list raises inside verify
    assert r.status is Status.ERROR and h.limiter.state.failures == 1


def test_status_reports_challenge_flag() -> None:
    off = Harness([])
    assert not off.send(MsgType.STATUS).challenge_required
    on = Harness([])
    on.core._flags = 1  # what challenge_required=True sets
    assert on.send(MsgType.STATUS).challenge_required


def test_garbage_request_gets_error() -> None:
    h = Harness([])
    raw = h.core.handle(b"garbage", SESSION)
    assert raw[:4] == b"FUr1" and raw[5] == Status.ERROR


# --- no face unlock before the first password/PIN unlock since start ----------

def test_not_armed_after_start_refuses_without_camera() -> None:
    # Windows' restart sign-in (ARSO) logs in and locks: a locked session right after boot.
    h = Harness([OK], armed=False)
    h.core.on_session_lock(SESSION)
    r = h.send()
    assert r.status is Status.DISABLED and r.reason is Reason.RATE_LIMITED
    assert h.results == [OK]  # camera never used
    assert h.send(MsgType.STATUS).status is Status.DISABLED


def test_first_password_unlock_arms() -> None:
    h = Harness([OK], armed=False)
    h.core.on_session_lock(SESSION)
    h.core.on_session_unlock(SESSION)  # password/PIN
    h.core.on_session_lock(SESSION)
    assert h.send(MsgType.STATUS).status is Status.OK
    assert h.send().status is Status.OK


def test_failed_verifies_before_arming_do_not_count() -> None:
    h = Harness([], armed=False)
    h.core.on_session_lock(SESSION)
    for _ in range(10):
        h.send()
    assert h.limiter.state.failures == 0 and h.limiter.allowed
