"""ONNX Runtime output must match the torch reference saved at conversion time.

Skipped when the converted models aren't present (e.g. a fresh checkout).
"""
import numpy as np
import pytest

from face_unlock.integrity import IntegrityError
from face_unlock.models import MINIFAS_V1SE, MINIFAS_V2, model_path, models_dir

PARITY = models_dir() / "minifasnet_parity.npz"


@pytest.mark.skipif(not PARITY.is_file(), reason="run tools/convert_minifasnet.py first")
@pytest.mark.parametrize("spec", [MINIFAS_V2, MINIFAS_V1SE], ids=lambda s: s.filename)
def test_onnx_matches_torch(spec) -> None:
    import onnxruntime as ort

    try:
        path = model_path(spec)
    except IntegrityError as e:
        pytest.fail(str(e))
    ref = np.load(PARITY, allow_pickle=False)
    sess = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
    out = sess.run(None, {sess.get_inputs()[0].name: ref["input"]})[0]
    assert out.shape == (1, 3)
    np.testing.assert_allclose(out, ref[spec.filename], rtol=1e-3, atol=1e-3)
