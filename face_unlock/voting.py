"""N-of-M multi-frame voting with early exit.

Per-frame decisions (match, liveness) are noisy. A verify succeeds when at
least `required` of `window` frames pass. We stop as soon as the outcome is
certain, which keeps the common case (you, in good light) fast.
"""
from __future__ import annotations

from enum import Enum


class Verdict(Enum):
    PENDING = "pending"
    PASS = "pass"
    FAIL = "fail"


class FrameVote:
    def __init__(self, required: int, window: int) -> None:
        if not 1 <= required <= window:
            raise ValueError(f"need 1 <= required <= window, got {required} of {window}")
        self.required = required
        self.window = window
        self.passes = 0
        self.seen = 0

    def add(self, passed: bool) -> Verdict:
        if self.seen >= self.window:
            raise RuntimeError("vote window already full")
        self.seen += 1
        self.passes += int(passed)
        return self.verdict

    @property
    def verdict(self) -> Verdict:
        if self.passes >= self.required:
            return Verdict.PASS
        remaining = self.window - self.seen
        if self.passes + remaining < self.required:
            return Verdict.FAIL
        return Verdict.PENDING
