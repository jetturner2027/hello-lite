import numpy as np
import pytest

from face_unlock.matching import EMBEDDING_DIM, Gallery, l2_normalize


def _rand(rng: np.random.Generator, n: int | None = None) -> np.ndarray:
    shape = (EMBEDDING_DIM,) if n is None else (n, EMBEDDING_DIM)
    return rng.standard_normal(shape).astype(np.float32)


def test_l2_normalize_unit_length() -> None:
    v = l2_normalize(np.array([3.0, 4.0]))
    assert np.isclose(np.linalg.norm(v), 1.0)


def test_l2_normalize_rejects_zero() -> None:
    with pytest.raises(ValueError):
        l2_normalize(np.zeros(4))


def test_same_identity_matches_and_stranger_does_not() -> None:
    rng = np.random.default_rng(0)
    identity = _rand(rng)
    gallery = Gallery(identity + 0.2 * _rand(rng, 15))
    probe_self = identity + 0.2 * _rand(rng)
    probe_other = _rand(rng)

    assert gallery.match(probe_self, threshold=0.363).matched
    assert not gallery.match(probe_other, threshold=0.363).matched


def test_scale_invariant() -> None:
    rng = np.random.default_rng(1)
    g = Gallery(_rand(rng, 5))
    p = _rand(rng)
    assert np.isclose(g.match(p, 0.5).score, g.match(10 * p, 0.5).score)


def test_top_k_is_mean_of_best() -> None:
    e = np.eye(EMBEDDING_DIM, dtype=np.float32)
    g = Gallery(e[:4])
    probe = e[0] + e[1]  # similarity 0.707 to e0 and e1, 0 to e2 and e3
    assert np.isclose(g.match(probe, 0.0, top_k=2).score, 1 / np.sqrt(2))
    assert np.isclose(g.match(probe, 0.0, top_k=4).score, 1 / np.sqrt(2) / 2)


def test_top_k_clamped_to_gallery_size() -> None:
    rng = np.random.default_rng(2)
    g = Gallery(_rand(rng, 2))
    g.match(_rand(rng), 0.0, top_k=10)  # must not raise


@pytest.mark.parametrize("shape", [(0, EMBEDDING_DIM), (3, 64)])
def test_gallery_rejects_bad_shape(shape: tuple[int, int]) -> None:
    with pytest.raises(ValueError):
        Gallery(np.ones(shape))


def test_probe_wrong_dim() -> None:
    g = Gallery(np.ones((1, EMBEDDING_DIM)))
    with pytest.raises(ValueError):
        g.match(np.ones(64), 0.0)
