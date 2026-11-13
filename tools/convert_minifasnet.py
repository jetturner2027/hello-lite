"""One-off: convert minivision's official MiniFASNet weights to ONNX.

Runs in a *throwaway* venv with torch==2.5.1 (CPU wheel index), onnx==1.17.0
(torch's exporter needs it) and numpy==1.26.4. Put that venv at a short path
such as %USERPROFILE%\\fuconv: onnx's bundled test files overflow MAX_PATH under
deep folders. Torch is never a runtime dependency.

Inputs come from minivision-ai/Silent-Face-Anti-Spoofing at the pinned commit
(Apache-2.0): src/model_lib/MiniFASNet.py and resources/anti_spoof_models/*.pth,
copied flat into --upstream. The .pth hashes below are checked before loading,
and loading uses weights_only=True so a tampered pickle can't run code.

    python -I tools/convert_minifasnet.py --upstream DIR --out models
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

UPSTREAM_COMMIT = "b6d5f04ad78778917853b25c778acef6d5626d15"
WEIGHTS = {
    # .pth filename: (sha256, factory name in MiniFASNet.py, output onnx name)
    "2.7_80x80_MiniFASNetV2.pth": (
        "a5eb02e1843f19b5386b953cc4c9f011c3f985d0ee2bb9819eea9a142099bec0",
        "MiniFASNetV2",
        "minifasnet_v2_2.7_80x80.onnx",
    ),
    "4_0_0_80x80_MiniFASNetV1SE.pth": (
        "84ee1d37d96894d5e82de5a57df044ef80a58be2b218b5ed7cdfd875ec2f5990",
        "MiniFASNetV1SE",
        "minifasnet_v1se_4.0_80x80.onnx",
    ),
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--upstream", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    import numpy as np
    import torch

    sys.path.insert(0, str(args.upstream.resolve()))
    import MiniFASNet  # upstream architecture definitions

    args.out.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(0)
    probe = rng.uniform(0, 255, size=(1, 3, 80, 80)).astype(np.float32)
    parity: dict[str, np.ndarray] = {"input": probe}

    for pth_name, (expected, factory, onnx_name) in WEIGHTS.items():
        pth = args.upstream / pth_name
        if sha256(pth) != expected:
            print(f"hash mismatch: {pth_name}", file=sys.stderr)
            return 1
        state = torch.load(pth, map_location="cpu", weights_only=True)
        state = {k.removeprefix("module."): v for k, v in state.items()}
        model = getattr(MiniFASNet, factory)(conv6_kernel=((80 + 15) // 16, (80 + 15) // 16))
        model.load_state_dict(state)
        model.eval()

        dst = args.out / onnx_name
        torch.onnx.export(
            model, torch.zeros(1, 3, 80, 80), str(dst), input_names=["input"], output_names=["logits"], opset_version=13
        )
        with torch.no_grad():
            parity[onnx_name] = model(torch.from_numpy(probe)).numpy()
        print(f"{onnx_name}  sha256={sha256(dst)}")

    np.savez(args.out / "minifasnet_parity.npz", **parity)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
