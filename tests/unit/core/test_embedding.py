"""Unit tests for embedding providers (spec 0014)."""

from __future__ import annotations

import math

import pytest

from ai_sre.core.knowledge.embedding import (
    EMBEDDING_DIM,
    BGESmallEmbedder,
    HashingEmbedder,
    build_embedder,
)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_hashing_embedding_shape_and_dtype() -> None:
    emb = HashingEmbedder()
    [vec] = await emb.embed_documents(["database connection pool exhausted"])
    assert len(vec) == EMBEDDING_DIM
    assert all(isinstance(x, float) for x in vec)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_hashing_is_deterministic() -> None:
    a = (await HashingEmbedder().embed_documents(["same text here"]))[0]
    b = (await HashingEmbedder().embed_documents(["same text here"]))[0]
    assert a == b


@pytest.mark.unit
@pytest.mark.asyncio
async def test_hashing_vectors_are_unit_normalised() -> None:
    [vec] = await HashingEmbedder().embed_documents(["some non empty content"])
    norm = math.sqrt(sum(x * x for x in vec))
    assert norm == pytest.approx(1.0, abs=1e-5)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_hashing_empty_text_is_zero_vector() -> None:
    [vec] = await HashingEmbedder().embed_documents([""])
    assert len(vec) == EMBEDDING_DIM
    assert all(x == 0.0 for x in vec)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_similar_text_closer_than_dissimilar() -> None:
    emb = HashingEmbedder()
    q = (await emb.embed_query("rollback the deployment"))[0:]
    [chunk_match] = await emb.embed_documents(["to rollback the deployment run helm"])
    [chunk_other] = await emb.embed_documents(["unrelated text about pizza toppings"])

    def cos(a: list[float], b: list[float]) -> float:
        return sum(x * y for x, y in zip(a, b, strict=True))

    assert cos(q, chunk_match) > cos(q, chunk_other)


@pytest.mark.unit
def test_build_embedder_selects_impl() -> None:
    assert isinstance(build_embedder("hashing"), HashingEmbedder)
    assert isinstance(build_embedder("bge"), BGESmallEmbedder)


@pytest.mark.unit
def test_build_embedder_rejects_unknown() -> None:
    with pytest.raises(ValueError):
        build_embedder("nope")
