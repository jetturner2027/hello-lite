from pathlib import Path

import pytest

from face_unlock.config import Config, load_config, parse_config
from face_unlock.integrity import IntegrityError, sha256_file, verify_file


def test_defaults_when_missing(tmp_path: Path) -> None:
    assert load_config(tmp_path / "nope.toml") == Config()


def test_example_config_parses() -> None:
    example = Path(__file__).parent.parent / "config.example.toml"
    assert load_config(example) == Config()  # example documents the defaults


def test_config_with_bom(tmp_path: Path) -> None:
    p = tmp_path / "c.toml"
    p.write_bytes(b"\xef\xbb\xbf[service]\nallow_test_clients = true\n")
    assert load_config(p).service.allow_test_clients is True


def test_override_and_int_to_float() -> None:
    cfg = parse_config({"match": {"threshold": 1}, "vote": {"required": 2}})
    assert cfg.match.threshold == 1.0
    assert cfg.vote.required == 2


@pytest.mark.parametrize(
    "data",
    [
        {"match": {"treshold": 0.5}},  # typo
        {"bogus": {}},
        {"match": {"threshold": "high"}},
        {"vote": {"required": 9, "window": 5}},
        {"camera": {"index": True}},
    ],
)
def test_rejects_bad_config(data: dict) -> None:
    with pytest.raises(ValueError):
        parse_config(data)


def test_verify_file(tmp_path: Path) -> None:
    f = tmp_path / "m.onnx"
    f.write_bytes(b"model")
    good = sha256_file(f)
    assert verify_file(f, good.upper()) == f
    with pytest.raises(IntegrityError):
        verify_file(f, "0" * 64)
    with pytest.raises(IntegrityError):
        verify_file(tmp_path / "missing.onnx", good)
