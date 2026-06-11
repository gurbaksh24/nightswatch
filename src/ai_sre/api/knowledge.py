"""Knowledge-base routes (spec 0014).

    * POST   /v1/knowledge/docs        — multipart upload (Markdown / text / PDF)
    * GET    /v1/knowledge/docs        — list the tenant's docs
    * GET    /v1/knowledge/docs/{id}   — one doc
    * DELETE /v1/knowledge/docs/{id}   — soft-delete a doc

Upload is chunked + embedded synchronously in the request (BGE on CPU for a few
kB is fast; the hashing embedder is instant). Large corpora would move this to
the worker — out of scope here.
"""

from __future__ import annotations

import io
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status

from ai_sre.api.deps import get_knowledge_service
from ai_sre.config import get_settings
from ai_sre.core.knowledge.service import KnowledgeDocInput, KnowledgeService
from ai_sre.models.knowledge import KnowledgeDoc
from ai_sre.schemas.knowledge import KnowledgeDocResponse

router = APIRouter()

_ALLOWED_KINDS = {"runbook", "postmortem"}


def _bad_request(code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={"code": code, "message": message},
    )


def _extract_pdf_text(data: bytes) -> str:
    from pypdf import PdfReader  # local import: only the PDF path needs it

    reader = PdfReader(io.BytesIO(data))
    return "\n\n".join((page.extract_text() or "") for page in reader.pages)


def _decode_upload(
    *, filename: str, content_type: str | None, data: bytes
) -> tuple[str, bool]:
    """Return (text, is_markdown). Raises 400 on undecodable / empty content."""
    name = (filename or "").lower()
    is_pdf = name.endswith(".pdf") or (content_type == "application/pdf")
    if is_pdf:
        text = _extract_pdf_text(data)
        return text, False  # PDFs go through the fixed-token (non-heading) path

    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _bad_request(
            "knowledge.undecodable", "File is not valid UTF-8 text or a PDF."
        ) from exc
    # Both Markdown and plain text use the heading-aware path: plain text simply
    # has no ATX headings, so it collapses to a single section.
    return text, True


@router.post(
    "/knowledge/docs",
    status_code=status.HTTP_201_CREATED,
    response_model=KnowledgeDocResponse,
    summary="Upload a runbook / postmortem (Markdown, text, or PDF).",
)
async def upload_doc(
    file: UploadFile = File(...),
    kind: str = Form(...),
    title: str | None = Form(default=None),
    service: KnowledgeService = Depends(get_knowledge_service),
) -> KnowledgeDocResponse:
    if kind not in _ALLOWED_KINDS:
        raise _bad_request(
            "knowledge.bad_kind",
            f"kind must be one of {sorted(_ALLOWED_KINDS)}.",
        )

    data = await file.read()
    max_bytes = get_settings().knowledge_max_upload_bytes
    if len(data) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail={
                "code": "knowledge.too_large",
                "message": f"Document exceeds the {max_bytes}-byte limit.",
            },
        )
    if not data:
        raise _bad_request("knowledge.empty", "Uploaded file is empty.")

    text, is_markdown = _decode_upload(
        filename=file.filename or "", content_type=file.content_type, data=data
    )
    if not text.strip():
        raise _bad_request(
            "knowledge.empty", "Document has no extractable text content."
        )

    doc = await service.ingest(
        KnowledgeDocInput(
            title=title or file.filename or "untitled",
            kind=kind,
            text=text,
            is_markdown=is_markdown,
        )
    )
    return _to_response(doc)


@router.get(
    "/knowledge/docs",
    response_model=list[KnowledgeDocResponse],
    summary="List the tenant's knowledge documents.",
)
async def list_docs(
    service: KnowledgeService = Depends(get_knowledge_service),
) -> list[KnowledgeDocResponse]:
    docs = await service.repo.list_docs()
    return [_to_response(d) for d in docs]


@router.get(
    "/knowledge/docs/{doc_id}",
    response_model=KnowledgeDocResponse,
    summary="Fetch one knowledge document.",
)
async def get_doc(
    doc_id: UUID,
    service: KnowledgeService = Depends(get_knowledge_service),
) -> KnowledgeDocResponse:
    doc = await service.repo.get_doc(doc_id)
    if doc is None:
        raise _not_found()
    return _to_response(doc)


@router.delete(
    "/knowledge/docs/{doc_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Soft-delete a knowledge document.",
)
async def delete_doc(
    doc_id: UUID,
    service: KnowledgeService = Depends(get_knowledge_service),
) -> None:
    if not await service.repo.soft_delete_doc(doc_id):
        raise _not_found()


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": "knowledge.not_found", "message": "Document not found."},
    )


def _to_response(doc: KnowledgeDoc) -> KnowledgeDocResponse:
    # Map the ORM row to the DTO (extra_metadata -> metadata).
    return KnowledgeDocResponse(
        id=doc.id,
        kind=doc.kind,
        title=doc.title,
        metadata=doc.extra_metadata or {},
        created_at=doc.created_at,
    )
