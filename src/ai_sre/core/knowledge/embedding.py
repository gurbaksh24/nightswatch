"""Embedding providers (spec 0014).

``EmbeddingProvider`` is the ABC that ``KnowledgeService`` depends on; the
concrete implementation is chosen at the composition root and injected.

Two implementations ship:

    * :class:`HashingEmbedder` — deterministic feature-hashing bag-of-words,
      numpy-only, **no model or network dependency**. It is the default so the
      service works out-of-the-box (and in tests / CI) without downloading a
      ~130 MB model or pulling in torch. Quality is obviously lower than a real
      embedder, but cosine ranking still surfaces the chunk that shares the most
      terms with the query — enough for the spec's DoD and for environments
      where heavy ML deps aren't available.
    * :class:`BGESmallEmbedder` — ``BAAI/bge-small-en-v1.5`` via
      ``sentence-transformers`` (the production default per the LLD). The import
      is deferred to first use so this module stays importable without torch;
      install the ``embeddings`` optional extra and set
      ``AI_SRE_EMBEDDING_PROVIDER=bge`` to enable it.

Vectors are L2-normalised so a cosine-distance index (``<=>``) ranks them
correctly.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

EMBEDDING_DIM = 384

_TOKEN_RE = re.compile(r"[a-z0-9]+")


class EmbeddingProvider(ABC):
    """Turn text into fixed-dimension, cosine-ready vectors."""

    name: str = "base"
    dim: int = EMBEDDING_DIM

    @abstractmethod
    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of document chunks."""

    async def embed_query(self, text: str) -> list[float]:
        """Embed a search query. Override when the model wants a query prefix."""
        return (await self.embed_documents([text]))[0]

    async def warm_up(self) -> None:
        """Pre-load any heavy model. No-op unless overridden."""
        return None


class HashingEmbedder(EmbeddingProvider):
    """Deterministic bag-of-words feature hashing → unit vector.

    Uses BLAKE2b (not Python's salted ``hash()``) so embeddings are stable
    across processes and runs.
    """

    name = "hashing"

    def __init__(self, dim: int = EMBEDDING_DIM) -> None:
        self.dim = dim

    def _embed_one(self, text: str) -> list[float]:
        vec = np.zeros(self.dim, dtype=np.float32)
        for tok in _TOKEN_RE.findall(text.lower()):
            digest = hashlib.blake2b(tok.encode("utf-8"), digest_size=8).digest()
            idx = int.from_bytes(digest[:4], "little") % self.dim
            sign = 1.0 if digest[4] & 1 else -1.0
            vec[idx] += sign
        norm = float(np.linalg.norm(vec))
        if norm > 0.0:
            vec /= norm
        return vec.tolist()

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(t) for t in texts]


class BGESmallEmbedder(EmbeddingProvider):
    """``BAAI/bge-small-en-v1.5`` via sentence-transformers (lazy-loaded)."""

    name = "bge"
    _DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"
    _QUERY_PREFIX = "Represent this sentence for searching relevant passages: "

    def __init__(self, model_name: str | None = None) -> None:
        self.dim = EMBEDDING_DIM
        self._model_name = model_name or self._DEFAULT_MODEL
        self._model: SentenceTransformer | None = None

    def _load(self) -> SentenceTransformer:
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:  # pragma: no cover - env-dependent
                raise RuntimeError(
                    "sentence-transformers is not installed. Install the "
                    "'embeddings' extra or set AI_SRE_EMBEDDING_PROVIDER=hashing."
                ) from exc
            self._model = SentenceTransformer(self._model_name)
        return self._model

    async def warm_up(self) -> None:
        await asyncio.to_thread(self._load)

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        model = await asyncio.to_thread(self._load)
        arr = await asyncio.to_thread(
            lambda: model.encode(texts, normalize_embeddings=True)
        )
        return [row.tolist() for row in arr]

    async def embed_query(self, text: str) -> list[float]:
        prefixed = self._QUERY_PREFIX + text
        return (await self.embed_documents([prefixed]))[0]


def build_embedder(provider: str) -> EmbeddingProvider:
    """Construct an embedder by name. Raises on an unknown name.

    The composition root decides what to do on a missing optional dependency
    (it falls back to hashing); here we only map name → class.
    """
    if provider == "hashing":
        return HashingEmbedder()
    if provider == "bge":
        return BGESmallEmbedder()
    raise ValueError(f"Unknown embedding provider: {provider!r}")
