"""Attempt log: timestamp, result, reason. Never images, embeddings, or scores tied to secrets."""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def attempt_logger(log_dir: Path) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("faceunlock.attempts")
    if not logger.handlers:
        handler = RotatingFileHandler(log_dir / "attempts.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s\t%(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger


def log_attempt(
    logger: logging.Logger,
    kind: str,
    result: str,
    reason: str,
    frames: int = 0,
    ms: float = 0.0,
    stages: dict[str, float] | None = None,
) -> None:
    """`stages` is a per-stage time breakdown in ms (numbers only, e.g. camera, models)."""
    extra = "".join(f"\t{k}={v:.0f}" for k, v in (stages or {}).items())
    logger.info("%s\t%s\t%s\tframes=%d\tms=%.0f%s", kind, result, reason, frames, ms, extra)
