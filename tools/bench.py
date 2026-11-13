"""Per-stage latency and peak RAM, on the target laptop.

    python tools/bench.py                  # live camera
    python tools/bench.py --image me.jpg   # static image (no camera), repeated

Reports model load times, camera open, and median/p95 per stage for detect,
embed, and passive liveness, plus process peak working set.
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time
from collections.abc import Callable
from typing import TypeVar

import cv2
import psutil

from face_unlock.camera import Camera
from face_unlock.config import load_config
from face_unlock.detection import FaceDetector, FaceEmbedder

T = TypeVar("T")


def timed(fn: Callable[[], T]) -> tuple[T, float]:
    t0 = time.perf_counter()
    out = fn()
    return out, (time.perf_counter() - t0) * 1000


def summary(name: str, ms: list[float]) -> str:
    if not ms:
        return f"{name:<12} n/a"
    p95 = sorted(ms)[max(0, int(len(ms) * 0.95) - 1)]
    return f"{name:<12} median={statistics.median(ms):6.1f}ms  p95={p95:6.1f}ms  n={len(ms)}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--image")
    ap.add_argument("--frames", type=int, default=50)
    ap.add_argument("--no-liveness", action="store_true", help="skip if MiniFASNet isn't converted yet")
    args = ap.parse_args()

    cfg = load_config()
    proc = psutil.Process()
    rss0 = proc.memory_info().rss / 2**20

    det, t_det = timed(lambda: FaceDetector(cfg.detect.score_threshold))
    emb, t_emb = timed(FaceEmbedder)
    live, t_live = (None, 0.0)
    if not args.no_liveness:
        from face_unlock.liveness import PassiveLiveness

        live, t_live = timed(PassiveLiveness)
    print(f"load: yunet={t_det:.0f}ms sface={t_emb:.0f}ms minifasnet={t_live:.0f}ms")

    stages: dict[str, list[float]] = {"read": [], "detect": [], "embed": [], "liveness": []}

    def run(read: Callable[[], object]) -> None:
        for _ in range(args.frames):
            frame, ms = timed(read)
            stages["read"].append(ms)
            if frame is None:
                continue
            faces, ms = timed(lambda: det.detect(frame))
            stages["detect"].append(ms)
            if len(faces) != 1:
                continue
            _, ms = timed(lambda: emb.embed(frame, faces[0]))
            stages["embed"].append(ms)
            if live is not None:
                _, ms = timed(lambda: live.score(frame, faces[0]))
                stages["liveness"].append(ms)

    if args.image:
        img = cv2.imread(args.image)
        if img is None:
            print(f"cannot read {args.image}", file=sys.stderr)
            return 1
        img = cv2.resize(img, (cfg.camera.width, cfg.camera.height))
        run(lambda: img)
    else:
        t0 = time.perf_counter()
        with Camera(cfg.camera) as cam:
            print(f"camera open (incl. {cfg.camera.warmup_frames} warmup frames): {(time.perf_counter() - t0) * 1000:.0f}ms")
            run(cam.read)

    for name, ms in stages.items():
        print(summary(name, ms))
    per_frame = sum(statistics.median(v) for v in stages.values() if v)
    print(f"per-frame pipeline (sum of medians): {per_frame:.0f}ms")
    mem = proc.memory_info()
    print(f"RAM: start={rss0:.0f}MB now={mem.rss / 2**20:.0f}MB peak={mem.peak_wset / 2**20:.0f}MB")
    if not stages["embed"]:
        print("note: no single face seen, so embed/liveness weren't measured", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
