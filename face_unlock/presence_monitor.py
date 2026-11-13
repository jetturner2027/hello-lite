"""User-session walk-away lock. Run with pythonw.exe -m face_unlock.presence_monitor.

Only uses YuNet (230 KB model); no recognition, no liveness, no secrets.
"""
from __future__ import annotations

import argparse
import logging
import os
import time
from collections.abc import Callable
from pathlib import Path

import psutil

from face_unlock import winapi
from face_unlock.camera import Camera, CameraError
from face_unlock.config import Config, load_config
from face_unlock.detection import FaceDetector
from face_unlock.presence import Action, PresenceEnv, PresenceMonitor, Seen

log = logging.getLogger("faceunlock.presence")


def _make_check(cfg: Config) -> Callable[[], Seen]:
    detector: FaceDetector | None = None

    def check() -> Seen:
        nonlocal detector
        if detector is None:
            detector = FaceDetector(cfg.detect.score_threshold)
        try:
            with Camera(cfg.camera) as cam:
                for _ in range(cfg.presence.frames_per_check):
                    frame = cam.read()
                    if frame is None:
                        return Seen.UNKNOWN
                    if detector.detect(frame):
                        return Seen.PRESENT
            return Seen.ABSENT
        except CameraError:
            return Seen.UNKNOWN

    return check


def _paused(cfg: Config) -> bool:
    if winapi.is_remote_session() or winapi.is_workstation_locked():
        return True
    names = {n.lower() for n in cfg.presence.pause_processes}
    if names:
        for p in psutil.process_iter(["name"]):
            if (p.info["name"] or "").lower() in names:
                return True
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description="Walk-away auto-lock")
    ap.add_argument("--config", type=Path)
    ap.add_argument("--dry-run", action="store_true", help="log instead of locking")
    args = ap.parse_args()

    log_dir = Path(os.environ.get("LOCALAPPDATA", ".")) / "FaceUnlock"
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=log_dir / "presence.log", level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    cfg = load_config(args.config)

    def lock() -> None:
        if args.dry_run:
            log.info("would lock (dry run)")
        else:
            winapi.lock_workstation()

    monitor = PresenceMonitor(
        cfg.presence,
        PresenceEnv(idle_seconds=winapi.idle_seconds, paused=lambda: _paused(cfg), check=_make_check(cfg), lock=lock),
    )
    log.info("started (idle_minutes=%s, dry_run=%s)", cfg.presence.idle_minutes, args.dry_run)
    while True:
        try:
            action, sleep_s = monitor.tick()
        except Exception:  # keep running; a transient Win32/camera error shouldn't kill the monitor
            log.exception("tick failed")
            action, sleep_s = None, cfg.presence.check_interval_s
        if action is Action.CHECKED or (action is Action.LOCKED and not args.dry_run):
            log.info("%s (absent_streak=%d)", action.value, monitor.absent_streak)
        time.sleep(sleep_s)


if __name__ == "__main__":
    raise SystemExit(main())
