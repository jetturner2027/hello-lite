"""Load the face models when the screen locks, drop them when it unlocks.

Measured on the i3 (2026-11-17): after the laptop sits idle for 20 min or more, a
verify waited 0.7-1.5 s for the models to load, and the first inference ran up to
1 s slower than warm. Loading (and running each model once) at lock time moves that
cost to when you walk away. The models stay in memory only while the session is
locked; unlocking drops them, so the unlocked desktop pays nothing.

Thread-safe: the SCM thread calls warm()/drop() on session events while the pipe
thread calls get() during a verify. A load runs on one background worker thread.
"""
from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Generic, TypeVar

T = TypeVar("T")


class ModelCache(Generic[T]):
    def __init__(self, load: Callable[[], T]) -> None:
        self._load = load
        self._lock = threading.Lock()
        self._future: Future[T] | None = None
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="models")

    def get(self) -> Future[T]:
        """The models, loaded or loading. Starts a load if there is none, or if the
        last one failed (so one bad load doesn't stick until the next unlock)."""
        with self._lock:
            f = self._future
            if f is None or (f.done() and f.exception() is not None):
                f = self._future = self._pool.submit(self._load)
            return f

    def warm(self) -> None:
        """Start loading in the background (session locked)."""
        self.get()

    def drop(self) -> None:
        """Forget the models (session unlocked). A verify already using them keeps its
        own references; a load still in flight finishes and is then discarded."""
        with self._lock:
            self._future = None

    @property
    def loaded(self) -> bool:
        with self._lock:
            return self._future is not None
