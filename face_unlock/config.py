"""Config loading: defaults in code, overrides from %ProgramData%\\FaceUnlock\\config.toml."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import Any


def data_dir() -> Path:
    return Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "FaceUnlock"


def default_config_path() -> Path:
    return data_dir() / "config.toml"


@dataclass(frozen=True)
class CameraConfig:
    index: int = 0
    width: int = 640
    height: int = 480
    warmup_frames: int = 3  # first frames after open are often dark/auto-exposing
    # Face-priority exposure (exposure.py): when the face is darker than dark_face_brightness
    # (grey level, 0-255) on auto exposure, e.g. a window behind you, step the manual
    # exposure up from exposure_start until the face reaches target_face_brightness.
    # Exposure values are the driver's log2 seconds: -6 = 1/64 s, -3 = 1/8 s.
    face_exposure: bool = True
    dark_face_brightness: float = 60.0  # 2026-11-10: normal light measured 82-87 (liveness ~1.00)
    target_face_brightness: float = 85.0
    exposure_start: float = -6.0
    exposure_max: float = -3.0  # longer blurs and drops the frame rate
    exposure_settle_frames: int = 3  # measured: a new exposure shows up on the 4th frame


@dataclass(frozen=True)
class MatchConfig:
    threshold: float = 0.363  # SFace cosine similarity; OpenCV reference value
    top_k: int = 3


@dataclass(frozen=True)
class VoteConfig:
    required: int = 3
    window: int = 5


@dataclass(frozen=True)
class DetectConfig:
    score_threshold: float = 0.9
    min_face_px: int = 80  # reject tiny/distant faces; their embeddings are unreliable
    # During verify, another face at least this fraction of the largest face's width fails
    # the attempt (photo/person beside you). Smaller background faces are ignored.
    rival_ratio: float = 0.6


@dataclass(frozen=True)
class LivenessConfig:
    # MiniFASNet "real" probability per frame (mean of V2 and V1SE). 0.5 since 2026-11-10:
    # Jet's real face scored median 0.72 (V1SE ~0.99, V2 ~0.38), 40/45 frames >= 0.5;
    # phone photos (bright, and dimmed/closer/tilted) scored 0.00 on all 75 frames.
    passive_threshold: float = 0.5
    # Active head-turn challenge. Off by default (Jet's call, 2026-11-09): look-and-go
    # convenience over resistance to video replay. See docs/SECURITY.md.
    challenge: bool = False
    turn_ratio: float = 0.25  # measured 2026-11-09: natural turns peak ~0.5-0.6, frontal noise <= 0.09
    frontal_ratio: float = 0.12


@dataclass(frozen=True)
class VerifyConfig:
    timeout_s: float = 8.0  # whole verify, including the human doing the head turn


@dataclass(frozen=True)
class PresenceConfig:
    idle_minutes: float = 3.0  # no input for this long before the camera is used at all
    check_interval_s: float = 20.0
    absent_checks_to_lock: int = 3
    frames_per_check: int = 5  # any face in any frame = present
    # RDP is detected automatically. Third-party remote tools (TeamViewer, AnyDesk...)
    # can't be detected reliably; list their process names to pause while they run.
    pause_processes: tuple[str, ...] = ()


@dataclass(frozen=True)
class ServiceConfig:
    max_failures: int = 5
    # Phase 4 testing only: let any SYSTEM process (not just LogonUI) use the pipe.
    allow_test_clients: bool = False


@dataclass(frozen=True)
class Config:
    camera: CameraConfig = field(default_factory=CameraConfig)
    detect: DetectConfig = field(default_factory=DetectConfig)
    match: MatchConfig = field(default_factory=MatchConfig)
    vote: VoteConfig = field(default_factory=VoteConfig)
    liveness: LivenessConfig = field(default_factory=LivenessConfig)
    verify: VerifyConfig = field(default_factory=VerifyConfig)
    presence: PresenceConfig = field(default_factory=PresenceConfig)
    service: ServiceConfig = field(default_factory=ServiceConfig)


def _apply(section: Any, overrides: dict[str, Any], name: str) -> Any:
    known = {f.name for f in fields(section)}
    unknown = set(overrides) - known
    if unknown:
        # Fail loudly: a typo in a security threshold should not silently use the default.
        raise ValueError(f"unknown keys in [{name}]: {sorted(unknown)}")
    typed = {}
    for key, value in overrides.items():
        expected = type(getattr(section, key))
        if expected is float and isinstance(value, int) and not isinstance(value, bool):
            value = float(value)
        if expected is tuple:
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                raise ValueError(f"[{name}].{key} must be a list of strings")
            value = tuple(value)
        if type(value) is not expected:
            raise ValueError(f"[{name}].{key} must be {expected.__name__}, got {type(value).__name__}")
        typed[key] = value
    return replace(section, **typed)


def parse_config(data: dict[str, Any]) -> Config:
    cfg = Config()
    unknown = set(data) - {f.name for f in fields(cfg)}
    if unknown:
        raise ValueError(f"unknown config sections: {sorted(unknown)}")
    updates = {name: _apply(getattr(cfg, name), section, name) for name, section in data.items()}
    cfg = replace(cfg, **updates)
    if not 1 <= cfg.vote.required <= cfg.vote.window:
        raise ValueError("vote.required must be between 1 and vote.window")
    if cfg.presence.absent_checks_to_lock < 1 or cfg.service.max_failures < 1:
        raise ValueError("presence.absent_checks_to_lock and service.max_failures must be >= 1")
    return cfg


def load_config(path: Path | None = None) -> Config:
    path = path or default_config_path()
    if not path.is_file():
        return Config()
    # utf-8-sig: Windows PowerShell 5.1 and older Notepad write a BOM, which tomllib rejects.
    return parse_config(tomllib.loads(path.read_bytes().decode("utf-8-sig")))
