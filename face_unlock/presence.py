"""Walk-away lock logic.

The camera is never touched while you're using the machine: only after
`idle_minutes` without keyboard/mouse input do we start checking. Any face in
any frame counts as present; we only want to know whether *someone* is
there, and being permissive here can only make us lock less, never lock you out.

A failed camera open (another app has it, e.g. a video call) is UNKNOWN,
which never advances the absent count.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from face_unlock.config import PresenceConfig


class Seen(Enum):
    PRESENT = "present"
    ABSENT = "absent"
    UNKNOWN = "unknown"


class Action(Enum):
    NONE = "none"
    CHECKED = "checked"
    LOCKED = "locked"
    PAUSED = "paused"


@dataclass
class PresenceEnv:
    idle_seconds: Callable[[], float]
    paused: Callable[[], bool]  # remote session, already locked, pause process running...
    check: Callable[[], Seen]
    lock: Callable[[], None]


MAX_SLEEP_S = 60.0


class PresenceMonitor:
    def __init__(self, cfg: PresenceConfig, env: PresenceEnv) -> None:
        self._cfg = cfg
        self._env = env
        self.absent_streak = 0

    def tick(self) -> tuple[Action, float]:
        """Run one step. Returns what happened and how long to sleep before the next."""
        idle_threshold = self._cfg.idle_minutes * 60
        if self._env.paused():
            self.absent_streak = 0
            return Action.PAUSED, self._cfg.check_interval_s

        idle = self._env.idle_seconds()
        if idle < idle_threshold:
            self.absent_streak = 0
            # Sleep until the idle threshold could first be reached, but wake periodically anyway.
            return Action.NONE, min(MAX_SLEEP_S, idle_threshold - idle + 1)

        seen = self._env.check()
        if seen is Seen.PRESENT:
            self.absent_streak = 0
        elif seen is Seen.ABSENT:
            self.absent_streak += 1

        if self.absent_streak >= self._cfg.absent_checks_to_lock:
            self.absent_streak = 0
            # Re-check right before locking: the user may have come back during the camera check.
            if self._env.idle_seconds() >= idle_threshold and not self._env.paused():
                self._env.lock()
                return Action.LOCKED, self._cfg.check_interval_s
        return Action.CHECKED, self._cfg.check_interval_s
