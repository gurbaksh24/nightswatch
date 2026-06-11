"""Integration tests for knowledge ingestion + search (spec 0014).

Uses the default hashing embedder (no torch / model download), so these run
anywhere the integration Postgres is available.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient, Response
from sqlalchemy.ext.asyncio import AsyncEngine

from ai_sre.api.deps import _get_envelope_crypto, get_embedder
from ai_sre.config import get_settings
from ai_sre.core.knowledge.embedding import HashingEmbedder
from ai_sre.core.knowledge.repository import KnowledgeRepository
from ai_sre.core.knowledge.service import KnowledgeService
from ai_sre.core.tenant.api_key_service import ApiKeyService
from ai_sre.core.tenant.repository import ApiKeyRepository, TenantRepository
from ai_sre.core.tenant.service import TenantService
from ai_sre.db import session_scope
from ai_sre.utils.crypto import random_base64_key

_RUNBOOK = """# Checkout Runbook

## Database
If the checkout database connection pool is exhausted, restart the pgbouncer pods.

## Cache
When the redis cache is cold, warm it by replaying the last hour of events.

## Networking
For TLS handshake failures, rotate the ingress certificate immediately.
"""


@pytest.fixture(autouse=True)
def _settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_SRE_TENANT_ENCRYPTION_KEY", random_base64_key())
    monkeypatch.setenv("AI_SRE_EMBEDDING_PROVIDER", "hashing")
    get_settings.cache_clear()
    _get_envelope_crypto.cache_clear()
    get_embedder.cache_clear()


async def _seed_tenant(*, slug: str) -> tuple[str, str]:
    """Create a tenant + API key. Returns (tenant_id, api_key)."""
    async with session_scope() as session:
        tenant = await TenantService(TenantRepository(session)).create(name=slug, slug=slug)
        issued = await ApiKeyService(
            ApiKeyRepository(session), TenantRepository(session)
        ).issue(tenant.id, name="bootstrap")
        return str(tenant.id), issued.key


async def _upload(client: AsyncClient, key: str, *, content: bytes, kind: str = "runbook",
                  filename: str = "runbook.md", title: str = "Checkout Runbook") -> Response:
    return await client.post(
        "/v1/knowledge/docs",
        headers={"Authorization": f"Bearer {key}"},
        files={"file": (filename, content, "text/markdown")},
        data={"kind": kind, "title": title},
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_upload_list_get_delete(client: AsyncClient, db_engine: AsyncEngine) -> None:
    _, key = await _seed_tenant(slug="acme")
    headers = {"Authorization": f"Bearer {key}"}

    resp = await _upload(client, key, content=_RUNBOOK.encode())
    assert resp.status_code == 201, resp.text
    doc = resp.json()
    doc_id = doc["id"]
    assert doc["kind"] == "runbook"
    assert doc["title"] == "Checkout Runbook"

    listed = await client.get("/v1/knowledge/docs", headers=headers)
    assert listed.status_code == 200
    assert any(d["id"] == doc_id for d in listed.json())

    got = await client.get(f"/v1/knowledge/docs/{doc_id}", headers=headers)
    assert got.status_code == 200
    assert got.json()["id"] == doc_id

    deleted = await client.delete(f"/v1/knowledge/docs/{doc_id}", headers=headers)
    assert deleted.status_code == 204

    # Soft-deleted → gone from get + list.
    assert (await client.get(f"/v1/knowledge/docs/{doc_id}", headers=headers)).status_code == 404
    relisted = await client.get("/v1/knowledge/docs", headers=headers)
    assert all(d["id"] != doc_id for d in relisted.json())


@pytest.mark.integration
@pytest.mark.asyncio
async def test_search_returns_relevant_chunk_top1(
    client: AsyncClient, db_engine: AsyncEngine
) -> None:
    tenant_id, key = await _seed_tenant(slug="acme")
    resp = await _upload(client, key, content=_RUNBOOK.encode())
    assert resp.status_code == 201

    # No search route (search is for LLM tools); exercise the service directly
    # against the real pgvector index.
    import uuid

    async with session_scope() as session:
        svc = KnowledgeService(
            KnowledgeRepository(session, uuid.UUID(tenant_id)), HashingEmbedder()
        )
        hits = await svc.search("database connection pool exhausted", k=3)

    assert hits, "expected at least one search hit"
    assert "connection pool is exhausted" in hits[0].text
    assert hits[0].headings == ["Checkout Runbook", "Database"]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_search_is_tenant_isolated(client: AsyncClient, db_engine: AsyncEngine) -> None:
    _, a_key = await _seed_tenant(slug="a")
    b_tenant_id, _ = await _seed_tenant(slug="b")
    assert (await _upload(client, a_key, content=_RUNBOOK.encode())).status_code == 201

    import uuid

    async with session_scope() as session:
        svc = KnowledgeService(
            KnowledgeRepository(session, uuid.UUID(b_tenant_id)), HashingEmbedder()
        )
        hits = await svc.search("database connection pool exhausted", k=5)
    assert hits == []


@pytest.mark.integration
@pytest.mark.asyncio
async def test_empty_upload_rejected_400(client: AsyncClient, db_engine: AsyncEngine) -> None:
    _, key = await _seed_tenant(slug="acme")
    resp = await _upload(client, key, content=b"")
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "knowledge.empty"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_bad_kind_rejected_400(client: AsyncClient, db_engine: AsyncEngine) -> None:
    _, key = await _seed_tenant(slug="acme")
    resp = await _upload(client, key, content=_RUNBOOK.encode(), kind="nonsense")
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "knowledge.bad_kind"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_too_large_rejected_413(
    client: AsyncClient, db_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AI_SRE_KNOWLEDGE_MAX_UPLOAD_BYTES", "16")
    get_settings.cache_clear()
    _, key = await _seed_tenant(slug="acme")
    resp = await _upload(client, key, content=b"x" * 64)
    assert resp.status_code == 413
    assert resp.json()["detail"]["code"] == "knowledge.too_large"
