"""Request handling for face_service, independent of pipes and the SCM.

The Windows-specific shell (service.py) feeds it raw request bytes and
session events; everything security-relevant is decided here and unit-tested.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable

from face_unlock.audit import log_attempt
from face_unlock.liveness import Direction
from face_unlock.protocol import (
    FLAG_CHALLENGE, MsgType, ProtocolError, Request, Response, Status, encode_response, parse_request,
)
from face_unlock.ratelimit import FailureLimiter
from face_unlock.verifier import Reason, VerifyResult

# An unlock event this soon after a successful face verify is attributed to face unlock.
FACE_UNLOCK_ATTRIBUTION_S = 30.0

VerifyFn = Callable[[Direction | None], VerifyResult]


class FaceServiceCore:
    def __init__(
        self,
        limiter: FailureLimiter,
        verify: VerifyFn,
        audit: logging.Logger,
        clock: Callable[[], float] = time.monotonic,
        challenge_required: bool = False,
    ) -> None:
        self._limiter = limiter
        self._verify = verify
        self._audit = audit
        self._clock = clock
        self._flags = FLAG_CHALLENGE if challenge_required else 0
        self._locked_sessions: set[int] = set()
        self._last_face_success: float | None = None
        # Face stays off until a password/PIN unlock has happened since this process started:
        # "no first logon after restart". Needed because Windows' automatic restart sign-in
        # (ARSO) logs you in and locks, so the first screen after a reboot is a lock screen.
        # A service restart has the same effect, which fails closed.
        self._armed = False

    # --- session events (from SERVICE_CONTROL_SESSIONCHANGE) -----------------

    def on_session_lock(self, session_id: int) -> None:
        self._locked_sessions.add(session_id)

    def on_session_unlock(self, session_id: int) -> None:
        self._locked_sessions.discard(session_id)
        recent = self._last_face_success is not None and (
            self._clock() - self._last_face_success <= FACE_UNLOCK_ATTRIBUTION_S
        )
        self._last_face_success = None
        if not recent:
            # Unlocked by password/PIN: that's the "password logon" that re-arms face unlock.
            if not self._armed:
                log_attempt(self._audit, "arm", "ok", "first password/PIN unlock since start")
            elif not self._limiter.allowed or self._limiter.state.failures:
                log_attempt(self._audit, "rearm", "ok", "non-face unlock")
            self._armed = True
            self._limiter.password_logon()

    def on_session_logoff(self, session_id: int) -> None:
        self._locked_sessions.discard(session_id)

    # --- requests -------------------------------------------------------------

    def handle(self, raw: bytes, console_session: int) -> bytes:
        try:
            req = parse_request(raw)
        except ProtocolError as e:
            log_attempt(self._audit, "request", "reject", f"bad_request: {e}")
            # Echo nothing meaningful: without a parsed nonce the client will reject this anyway.
            return encode_response(Response(MsgType.STATUS, Status.ERROR, Reason.BAD_REQUEST, b"\0" * 32))
        return encode_response(self._dispatch(req, console_session))

    def _dispatch(self, req: Request, console_session: int) -> Response:
        def reply(status: Status, reason: Reason = Reason.NONE) -> Response:
            return Response(req.type, status, reason, req.nonce, self._flags)

        if req.type is MsgType.STATUS:
            return reply(Status.OK if self._limiter.allowed and self._armed else Status.DISABLED)

        if req.type is MsgType.REPORT_LOGON_FAILED:
            # The stored password was rejected (changed password?). Stop before we
            # rack up bad-password counts toward an account lockout policy.
            self._last_face_success = None
            self._limiter.disable("stored password rejected by Windows")
            log_attempt(self._audit, "logon", "fail", "stored password rejected; face unlock disabled")
            return reply(Status.OK)

        # VERIFY
        if not self._limiter.allowed:
            log_attempt(self._audit, "verify", "refused", f"disabled: {self._limiter.state.disabled_reason}")
            return reply(Status.DISABLED, Reason.RATE_LIMITED)
        if not self._armed:
            # Same reply as the lockout: the tile then says "unlock once with your password".
            log_attempt(self._audit, "verify", "refused", "no password/PIN unlock since start")
            return reply(Status.DISABLED, Reason.RATE_LIMITED)
        if console_session not in self._locked_sessions:
            # Unlock only: never serve cold-boot logon or an unlocked desktop.
            log_attempt(self._audit, "verify", "refused", "console session not locked")
            return reply(Status.FAIL, Reason.NOT_LOCKED)

        start = self._clock()
        try:
            result = self._verify(req.direction)
        except Exception as e:  # noqa: BLE001 - the service must answer, never die mid-request
            log_attempt(self._audit, "verify", "error", type(e).__name__)
            self._limiter.record_failure()
            return reply(Status.ERROR, Reason.INTERNAL)
        ms = (self._clock() - start) * 1000

        if result.ok:
            self._limiter.record_success()
            self._last_face_success = self._clock()
            log_attempt(self._audit, "verify", "ok", "", result.frames, ms, result.timings_ms)
            return reply(Status.OK)

        # Environmental failures (no camera, nobody in frame) don't count toward lockout:
        # they aren't evidence of an attack and would just lock you out of a feature.
        # NOT_LOCKED here means the check was cancelled because you unlocked another way.
        if result.reason not in (Reason.CAMERA_ERROR, Reason.NO_FACE, Reason.NOT_ENROLLED, Reason.NOT_LOCKED):
            self._limiter.record_failure()
        log_attempt(self._audit, "verify", "fail", result.reason.name, result.frames, ms, result.timings_ms)
        if not self._limiter.allowed:
            return reply(Status.DISABLED, Reason.RATE_LIMITED)
        return reply(Status.FAIL, result.reason)
