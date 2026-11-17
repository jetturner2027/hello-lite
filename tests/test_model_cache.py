"""Preload on lock, reuse during the lock, drop on unlock, retry after a failed load."""
from __future__ import annotations

import threading

import pytest

from face_unlock.model_cache import ModelCache
from face_unlock.service import _warm_up
from face_unlock.config import Config


class Loader:
    def __init__(self, fail_first: bool = False, gate: threading.Event | None = None) -> None:
        self.calls = 0
        self.fail_first = fail_first
        self.gate = gate

    def __call__(self) -> str:
        self.calls += 1
        if self.gate is not None:
            self.gate.wait(5)
        if self.fail_first and self.calls == 1:
            raise OSError("model file busy")
        return f"models#{self.calls}"


def test_warm_then_verify_reuses_the_preload() -> None:
    load = Loader()
    cache = ModelCache(load)
    cache.warm()
    assert cache.get().result(5) == "models#1"
    assert cache.get().result(5) == "models#1"  # a second verify on the same lock screen
    assert load.calls == 1


def test_verify_during_preload_waits_for_it_instead_of_loading_twice() -> None:
    gate = threading.Event()
    load = Loader(gate=gate)
    cache = ModelCache(load)
    cache.warm()
    pending = cache.get()  # verify arrives while the preload is still running
    gate.set()
    assert pending.result(5) == "models#1"
    assert load.calls == 1


def test_unlock_drops_and_next_lock_reloads() -> None:
    load = Loader()
    cache = ModelCache(load)
    cache.warm()
    cache.get().result(5)
    cache.drop()
    assert not cache.loaded
    cache.warm()
    assert cache.get().result(5) == "models#2"


def test_failed_load_is_retried_not_cached() -> None:
    load = Loader(fail_first=True)
    cache = ModelCache(load)
    with pytest.raises(OSError):
        cache.get().result(5)
    assert cache.get().result(5) == "models#2"


def test_no_preload_still_loads_on_demand() -> None:
    cache = ModelCache(Loader())
    assert not cache.loaded
    assert cache.get().result(5) == "models#1"


def test_warm_up_runs_each_model_once_and_never_raises() -> None:
    seen: list[str] = []

    class Det:
        def detect(self, frame: object) -> list[object]:
            seen.append("detect")
            return []

    class Emb:
        def embed(self, frame: object, face: object) -> None:
            seen.append("embed")

    class Live:
        def score(self, frame: object, face: object) -> float:
            seen.append("score")
            raise RuntimeError("bad crop")  # must be swallowed

    _warm_up(Config(), Det(), Emb(), Live())
    assert seen == ["detect", "embed", "score"]
