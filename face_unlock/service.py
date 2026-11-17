"""face_service: Windows service (LocalSystem) hosting the verify pipe.

Hosted by python.exe itself (servicemanager dispatch) rather than pywin32's
pythonservice.exe, so the credential provider has one fixed image path to pin:
    C:\\Program Files\\FaceUnlock\\python\\python.exe

Pipe hardening, and why each piece is there:
- DACL "D:P(A;;GA;;;SY)": only SYSTEM can open it. LogonUI (where the CP DLL
  lives) is SYSTEM; your user session is not.
- FILE_FLAG_FIRST_PIPE_INSTANCE + max 1 instance, created once and reused:
  if anyone else squats the name first, CreateNamedPipe fails and we refuse to
  run instead of sharing the name with an impostor.
- PIPE_REJECT_REMOTE_CLIENTS: no SMB access to the pipe.
- Client check: the connecting process must be LogonUI.exe (defense in depth
  on top of the DACL).
- Overlapped I/O with timeouts, so a client that connects and stalls can't
  wedge the single instance, and service stop is always responsive.
"""
from __future__ import annotations

import faulthandler
import gc
import logging
import os
import sys
import threading
import time
from collections.abc import Callable
from contextlib import ExitStack
from pathlib import Path

import pywintypes
import servicemanager
import win32event
import win32file
import win32pipe
import win32security
import win32service
import win32serviceutil
import winerror

from face_unlock import stores, winapi
from face_unlock.audit import attempt_logger, log_attempt
from face_unlock.camera import Camera, CameraError
from face_unlock.config import Config, data_dir, load_config
from face_unlock.exposure import expose_for_face
from face_unlock.liveness import Direction
from face_unlock.model_cache import ModelCache
from face_unlock.protocol import MESSAGE_LEN, PIPE_NAME
from face_unlock.ratelimit import FailureLimiter
from face_unlock.secure_store import ensure_secrets_dir
from face_unlock.service_core import FaceServiceCore
from face_unlock.verifier import Reason, VerifyResult

PIPE_SDDL = "D:P(A;;GA;;;SY)"
FILE_FLAG_FIRST_PIPE_INSTANCE = 0x00080000
PIPE_REJECT_REMOTE_CLIENTS = 0x00000008
LOGONUI = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "LogonUI.exe"
IO_TIMEOUT_MS = 2000
SERVICE_CONTROL_SESSIONCHANGE = 14
WTS_SESSION_LOGOFF, WTS_SESSION_LOCK, WTS_SESSION_UNLOCK = 6, 7, 8


class AfterReply:
    """Clean-up that can wait until the reply has been written to the pipe.

    Releasing the camera takes ~0.35 s (driver stream shutdown); doing it before
    replying would add that to every unlock. The pipe loop calls run() on every
    path, including errors, so deferred work always happens before the next request.
    """

    def __init__(self) -> None:
        self._fns: list[Callable[[], None]] = []

    def add(self, fn: Callable[[], None]) -> None:
        self._fns.append(fn)

    def run(self) -> None:
        fns, self._fns = self._fns, []
        for fn in fns:
            try:
                fn()
            except Exception:  # noqa: BLE001 - one failed clean-up mustn't skip the rest
                logging.getLogger("faceunlock.service").exception("after-reply clean-up failed")


def load_models(cfg: Config) -> tuple[object, object, object]:
    """Detector, embedder, liveness, each run once so the first real frame is fast."""
    from face_unlock.detection import FaceDetector, FaceEmbedder
    from face_unlock.liveness import PassiveLiveness

    models = (FaceDetector(cfg.detect.score_threshold), FaceEmbedder(), PassiveLiveness())
    _warm_up(cfg, *models)
    return models


def _warm_up(cfg: Config, detector: object, embedder: object, live: object) -> None:
    """ONNX Runtime and OpenCV DNN allocate buffers and pick kernels on the first call,
    which measured up to ~1 s extra on the i3. Pay that now, on a blank frame and a
    synthetic face box: no camera, no real face, nothing kept."""
    import numpy as np

    from face_unlock.detection import Face

    try:
        w, h = cfg.camera.width, cfg.camera.height
        frame = np.zeros((h, w, 3), np.uint8)
        s = min(w, h) / 3
        x, y = (w - s) / 2, (h - s) / 2
        cx, eye_y = x + s / 2, y + 0.4 * s
        raw = [x, y, s, s, cx - 0.2 * s, eye_y, cx + 0.2 * s, eye_y, cx, y + 0.6 * s,
               cx - 0.15 * s, y + 0.8 * s, cx + 0.15 * s, y + 0.8 * s, 0.99]
        face = Face(np.array(raw, np.float32))
        detector.detect(frame)  # type: ignore[attr-defined]
        embedder.embed(frame, face)  # type: ignore[attr-defined]
        live.score(frame, face)  # type: ignore[attr-defined]
    except Exception as e:  # noqa: BLE001 - warm-up is an optimization; never fail a load over it
        logging.getLogger("faceunlock.service").warning("model warm-up skipped: %s", type(e).__name__)


def make_verify_fn(
    cfg: Config,
    after_reply: AfterReply,
    cancelled: Callable[[], bool] = lambda: False,
    models: ModelCache | None = None,
):
    """The gallery (biometric data) is loaded per request and dropped after. Models
    come from `models`, which the service preloads when the session locks and drops
    when it unlocks, so the unlocked, idle service holds neither."""
    from face_unlock.verifier import Verifier

    cache = models if models is not None else ModelCache(lambda: load_models(cfg))

    def verify(direction: Direction | None) -> VerifyResult:
        try:
            gallery = stores.load_gallery(dev=False)
        except FileNotFoundError:
            return VerifyResult(False, Reason.NOT_ENROLLED)
        # Usually already loaded (preloaded at lock). If not, the load (~1.2 s) runs on
        # the cache's worker thread while this thread starts the camera (~0.5 s). The
        # camera stays on this thread because DirectShow objects are tied to the
        # creating thread (the pipe loop runs after_reply on this same thread).
        loading = cache.get()
        verifier = None
        camera = ExitStack()
        # Per-stage times for the attempt log (numbers only), to see where a slow
        # unlock spends its time: camera start, waiting on models, exposure, voting.
        stages: dict[str, float] = {}
        t = time.monotonic()

        def lap(name: str) -> None:
            nonlocal t
            now = time.monotonic()
            stages[name] = (now - t) * 1000
            t = now

        try:
            cam = camera.enter_context(Camera(cfg.camera))
            lap("camera")
            detector, embedder, live = loading.result()
            lap("models")  # extra wait only; loading overlaps the camera start
            if cfg.camera.face_exposure:
                exp = expose_for_face(cam, detector, cfg.camera)
                lap("exposure")
                if exp.exposure is not None:
                    stages["exp"] = exp.exposure  # the camera setting chosen (log2 s), not a face value
            verifier = Verifier(cfg, detector, embedder, live, cancelled=cancelled)
            result = verifier.verify(cam, gallery, direction)
            result.timings_ms = {**stages, **result.timings_ms}
            # Only a verify that finished normally defers the release; on any exception
            # the finally below closes the camera right away.
            after_reply.add(camera.pop_all().close)
            after_reply.add(gc.collect)
            return result
        except CameraError:
            return VerifyResult(False, Reason.CAMERA_ERROR)
        finally:
            camera.close()  # no-op once handed to after_reply
            del verifier, gallery

    return verify


def _wait(handle: int, ov: pywintypes.OVERLAPPED, stop: int, timeout_ms: int) -> int | None:
    """Wait for an overlapped op; returns bytes transferred, or None on stop/timeout (op cancelled)."""
    rc = win32event.WaitForMultipleObjects([ov.hEvent, stop], False, timeout_ms)
    if rc != win32event.WAIT_OBJECT_0:
        win32file.CancelIo(handle)
        return None
    return win32file.GetOverlappedResult(handle, ov, False)


def _overlapped() -> pywintypes.OVERLAPPED:
    ov = pywintypes.OVERLAPPED()
    ov.hEvent = win32event.CreateEvent(None, True, False, None)
    return ov


class FaceUnlockService(win32serviceutil.ServiceFramework):
    _svc_name_ = "FaceUnlockSvc"
    _svc_display_name_ = "Face Unlock Service"
    _svc_description_ = "Webcam face verification for the Face Unlock credential provider."
    _exe_name_ = sys.executable
    _exe_args_ = "-m face_unlock.service"

    def __init__(self, args: list[str]) -> None:
        super().__init__(args)
        self._stop = win32event.CreateEvent(None, True, False, None)
        self._lock = threading.Lock()  # SvcOtherEx runs on the SCM thread
        # Set on unlock/logoff, cleared on lock. Read by a running verify without taking
        # _lock (which that verify holds), so an unlock stops the camera within a frame.
        self._cancel = threading.Event()
        try:
            cfg = load_config()
            # Models are preloaded on lock and dropped on unlock (see model_cache.py).
            self._models: ModelCache = ModelCache(lambda: load_models(cfg))
            self._cfg = cfg
            ensure_secrets_dir()
            audit = attempt_logger(data_dir() / "logs")
            self._audit = audit
            limiter = FailureLimiter(stores.ss.secrets_dir() / "limiter.json", self._cfg.service.max_failures)
            self._after_reply = AfterReply()
            self._core = FaceServiceCore(
                limiter,
                make_verify_fn(self._cfg, self._after_reply, self._cancel.is_set, self._models),
                audit,
                challenge_required=self._cfg.liveness.challenge,
            )
        except Exception:
            # pywin32 swallows constructor exceptions silently; a bad config.toml lands here.
            logging.getLogger("faceunlock.service").exception("startup failed")
            raise

    def GetAcceptedControls(self) -> int:
        return super().GetAcceptedControls() | win32service.SERVICE_ACCEPT_SESSIONCHANGE

    def SvcOtherEx(self, control: int, event_type: int, data: object) -> None:
        logging.getLogger("faceunlock.service").info("control=%d event=%d data=%r", control, event_type, data)
        if control != SERVICE_CONTROL_SESSIONCHANGE:
            return
        session_id = data[0] if isinstance(data, tuple) else int(data)  # type: ignore[index, arg-type]
        # Before taking _lock: a verify in progress holds it until it finishes.
        if event_type == WTS_SESSION_LOCK:
            self._cancel.clear()
            # Preload only when face unlock could actually be used on this lock screen.
            if self._core.face_available:
                self._models.warm()
        elif event_type in (WTS_SESSION_UNLOCK, WTS_SESSION_LOGOFF):
            self._cancel.set()
            if self._models.loaded:
                self._models.drop()
                gc.collect()
        with self._lock:
            if event_type == WTS_SESSION_LOCK:
                self._core.on_session_lock(session_id)
            elif event_type == WTS_SESSION_UNLOCK:
                self._core.on_session_unlock(session_id)
            elif event_type == WTS_SESSION_LOGOFF:
                self._core.on_session_logoff(session_id)

    def SvcStop(self) -> None:
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        win32event.SetEvent(self._stop)

    def SvcDoRun(self) -> None:
        sa = pywintypes.SECURITY_ATTRIBUTES()
        sa.SECURITY_DESCRIPTOR = win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(
            PIPE_SDDL, win32security.SDDL_REVISION_1
        )
        try:
            pipe = win32pipe.CreateNamedPipe(
                PIPE_NAME,
                win32pipe.PIPE_ACCESS_DUPLEX | win32file.FILE_FLAG_OVERLAPPED | FILE_FLAG_FIRST_PIPE_INSTANCE,
                win32pipe.PIPE_TYPE_MESSAGE | win32pipe.PIPE_READMODE_MESSAGE | win32pipe.PIPE_WAIT
                | PIPE_REJECT_REMOTE_CLIENTS,
                1, MESSAGE_LEN, MESSAGE_LEN, 0, sa,
            )
        except pywintypes.error as e:
            logging.getLogger("faceunlock.service").error("cannot create pipe (squatted?): %s", e)
            return
        # Not servicemanager.LogInfoMsg: with pywin32 308 hosted in python.exe it writes the
        # event and then crashes python312.dll (access violation, same offset every time).
        log = logging.getLogger("faceunlock.service")
        log.info("pipe created; serving")
        try:
            while win32event.WaitForSingleObject(self._stop, 0) != win32event.WAIT_OBJECT_0:
                try:
                    self._serve_one(pipe)
                except pywintypes.error as e:
                    # One client's I/O failing must never take the service down. The usual
                    # case: you typed your PIN while a background face check was running,
                    # so LogonUI closed the pipe before our reply (ERROR_NO_DATA, 232).
                    # _serve_one's finally has already disconnected; wait a moment so a
                    # persistent error can't spin, then serve the next client.
                    log.warning("client I/O failed (%s %s); continuing", e.winerror, e.funcname)
                    win32event.WaitForSingleObject(self._stop, 100)
        except Exception:
            log.exception("serve loop failed")
            failed = True
        else:
            failed = False
        finally:
            pipe.Close()
            log.info("serve loop exited")
        if failed:
            # Returning would report a clean SERVICE_STOPPED, and the SCM never restarts a
            # clean stop. Exiting now, while still RUNNING, counts as a crash, so the
            # recovery actions set by install_service.ps1 apply (restart after 5 s, then
            # 30 s). Face unlock stays off after the restart until a PIN/password unlock.
            log.critical("exiting so the service manager restarts the service")
            logging.shutdown()
            os._exit(1)

    def _serve_one(self, pipe: int) -> None:
        ov = _overlapped()
        rc = win32pipe.ConnectNamedPipe(pipe, ov)
        if rc == winerror.ERROR_IO_PENDING:
            if _wait(pipe, ov, self._stop, win32event.INFINITE) is None:
                return
        elif rc not in (0, winerror.ERROR_PIPE_CONNECTED):
            return
        try:
            if not self._client_allowed(pipe):
                return
            buf = win32file.AllocateReadBuffer(MESSAGE_LEN + 1)
            ov = _overlapped()
            try:
                win32file.ReadFile(pipe, buf, ov)
                n = _wait(pipe, ov, self._stop, IO_TIMEOUT_MS)
            except pywintypes.error:  # ERROR_MORE_DATA: oversized message
                n = None
            if n is None:
                return
            with self._lock:
                response = self._core.handle(bytes(buf[:n]), winapi.active_console_session())
            ov = _overlapped()
            win32file.WriteFile(pipe, response, ov)
            if _wait(pipe, ov, self._stop, IO_TIMEOUT_MS) is None:
                return
            # The reply is on its way; now release the camera while the client reads it.
            self._after_reply.run()
            # Wait for the client to close (read fails with BROKEN_PIPE) so Disconnect
            # doesn't discard the unread response; bounded by the timeout.
            ov = _overlapped()
            try:
                win32file.ReadFile(pipe, win32file.AllocateReadBuffer(1), ov)
                _wait(pipe, ov, self._stop, IO_TIMEOUT_MS)
            except pywintypes.error:
                pass
        finally:
            self._after_reply.run()  # every exit path, so the camera never outlives a request
            win32pipe.DisconnectNamedPipe(pipe)

    def _client_allowed(self, pipe: int) -> bool:
        pid = win32pipe.GetNamedPipeClientProcessId(pipe)
        image = winapi.process_image_path(pid)
        if image and Path(image).resolve() == LOGONUI.resolve():
            return True
        if self._cfg.service.allow_test_clients:
            return True  # DACL still limits this to SYSTEM processes
        log_attempt(self._audit, "connect", "reject", f"client not LogonUI: {image}")
        return False


def _setup_diagnostics() -> None:
    """A service has no console: without this, crashes vanish with only "terminated unexpectedly".

    service.log gets Python exceptions from any thread; faulthandler writes native
    crash stacks to the same directory. Neither ever contains frames or secrets.
    """
    log_dir = data_dir() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=log_dir / "service.log", level=logging.INFO, format="%(asctime)s %(threadName)s %(levelname)s %(message)s"
    )
    fault = (log_dir / "service_fault.log").open("a", encoding="utf-8")
    faulthandler.enable(file=fault, all_threads=True)
    sys.excepthook = lambda t, v, tb: logging.getLogger("faceunlock.service").critical("uncaught", exc_info=(t, v, tb))
    threading.excepthook = lambda a: logging.getLogger("faceunlock.service").critical(
        "uncaught in thread %s", a.thread.name if a.thread else "?", exc_info=(a.exc_type, a.exc_value, a.exc_traceback)
    )


if __name__ == "__main__":
    if len(sys.argv) == 1:
        _setup_diagnostics()
        log = logging.getLogger("faceunlock.service")
        log.info("process start pid=%d exe=%s", os.getpid(), sys.executable)
        try:
            servicemanager.Initialize()
            servicemanager.PrepareToHostSingle(FaceUnlockService)
            servicemanager.StartServiceCtrlDispatcher()
            log.info("dispatcher returned normally")
        except BaseException:
            log.exception("dispatcher failed")
            raise
    else:
        win32serviceutil.HandleCommandLine(FaceUnlockService)
