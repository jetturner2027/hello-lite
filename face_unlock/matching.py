"""Embedding comparison against an enrolled gallery.

SFace produces 128-D embeddings. OpenCV's reference threshold for SFace with
cosine *similarity* is 0.363 (higher = more alike); we expose it as a tunable.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

EMBEDDING_DIM = 128

Embedding = NDArray[np.float32]


def l2_normalize(v: NDArray[np.floating]) -> Embedding:
    """Return v scaled to unit length, as float32 rows. Accepts (D,) or (N, D)."""
    arr = np.asarray(v, dtype=np.float32)
    norms = np.linalg.norm(arr, axis=-1, keepdims=True)
    if np.any(norms == 0):
        raise ValueError("cannot normalize a zero vector")
    return arr / norms


@dataclass(frozen=True)
class MatchResult:
    score: float  # mean cosine similarity of the top-k gallery samples
    threshold: float

    @property
    def matched(self) -> bool:
        return self.score >= self.threshold


class Gallery:
    """The enrolled embeddings for one user, stored normalized."""

    def __init__(self, embeddings: NDArray[np.floating]) -> None:
        arr = np.atleast_2d(np.asarray(embeddings, dtype=np.float32))
        if arr.shape[0] == 0 or arr.shape[1] != EMBEDDING_DIM:
            raise ValueError(f"expected (N, {EMBEDDING_DIM}) embeddings, got {arr.shape}")
        self._embeddings = l2_normalize(arr)

    def __len__(self) -> int:
        return self._embeddings.shape[0]

    @property
    def embeddings(self) -> Embedding:
        """Normalized (N, 128) copy, e.g. for re-saving to another store."""
        return self._embeddings.copy()

    def similarities(self, probe: NDArray[np.floating]) -> NDArray[np.float32]:
        p = l2_normalize(np.asarray(probe, dtype=np.float32).reshape(-1))
        if p.shape[0] != EMBEDDING_DIM:
            raise ValueError(f"probe must have {EMBEDDING_DIM} dims, got {p.shape[0]}")
        return self._embeddings @ p

    def match(self, probe: NDArray[np.floating], threshold: float, top_k: int = 3) -> MatchResult:
        """Score = mean of the k best similarities.

        Taking the single best sample lets one lucky (or badly enrolled) sample
        decide; averaging the whole gallery penalizes legitimate pose variety.
        Top-k sits in between.
        """
        sims = self.similarities(probe)
        k = max(1, min(top_k, len(sims)))
        top = np.partition(sims, -k)[-k:]
        return MatchResult(score=float(top.mean()), threshold=threshold)
