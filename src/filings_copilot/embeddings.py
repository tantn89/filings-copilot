"""Embedding backends behind one small protocol.

FastEmbed runs a quantized ONNX model locally (no API key, ~130 MB RAM), which keeps the
whole project runnable for free. HashingEmbedder is deterministic and dependency-free; it
is only meant for tests.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence
from typing import Protocol


class Embedder(Protocol):
    dimension: int

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class FastEmbedEmbedder:
    def __init__(self, model_name: str) -> None:
        from fastembed import TextEmbedding

        self._model = TextEmbedding(model_name=model_name)
        self.dimension = len(next(iter(self._model.embed(["probe"]))))

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [vector.tolist() for vector in self._model.embed(list(texts), batch_size=64)]

    def embed_query(self, text: str) -> list[float]:
        return [float(x) for x in next(iter(self._model.query_embed(text)))]


class HashingEmbedder:
    """Bag-of-words feature hashing, L2-normalised."""

    def __init__(self, dimension: int = 256) -> None:
        self.dimension = dimension

    def _embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension
        for token in re.findall(r"[a-z0-9]+", text.lower()):
            digest = hashlib.blake2b(token.encode(), digest_size=8).digest()
            vector[int.from_bytes(digest, "little") % self.dimension] += 1.0
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / norm for v in vector]

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)
