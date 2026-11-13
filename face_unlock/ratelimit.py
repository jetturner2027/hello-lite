"""Consecutive-failure lockout, persisted so restarting the service doesn't reset it."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class LimiterState:
    failures: int = 0
    disabled: bool = False
    disabled_reason: str = ""


class FailureLimiter:
    def __init__(self, path: Path | None, max_failures: int) -> None:
        self._path = path
        self._max = max_failures
        self.state = self._load()

    @property
    def allowed(self) -> bool:
        return not self.state.disabled

    def record_failure(self) -> None:
        self.state.failures += 1
        if self.state.failures >= self._max:
            self._disable(f"{self.state.failures} consecutive failures")
        self._save()

    def record_success(self) -> None:
        self.state.failures = 0
        self._save()

    def disable(self, reason: str) -> None:
        self._disable(reason)
        self._save()

    def password_logon(self) -> None:
        """Only a non-face logon re-enables face unlock."""
        self.state = LimiterState()
        self._save()

    def _disable(self, reason: str) -> None:
        self.state.disabled = True
        self.state.disabled_reason = reason

    def _load(self) -> LimiterState:
        if self._path is None or not self._path.is_file():
            return LimiterState()
        try:
            raw = json.loads(self._path.read_text("utf-8"))
            return LimiterState(
                failures=int(raw["failures"]), disabled=bool(raw["disabled"]), disabled_reason=str(raw["disabled_reason"])
            )
        except (ValueError, KeyError, TypeError):
            # Fail closed: an unreadable state file means face unlock stays off.
            return LimiterState(failures=self._max, disabled=True, disabled_reason="corrupt limiter state")

    def _save(self) -> None:
        if self._path is None:
            return
        tmp = self._path.with_name(self._path.name + ".tmp")
        tmp.write_text(json.dumps(asdict(self.state)), "utf-8")
        os.replace(tmp, self._path)
