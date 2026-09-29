"""Ingestion: bytes -> blocks -> chunks -> embeddings -> one transaction.

Runs inline in the upload request. For the small documents this app targets it
takes a few seconds, and it means there's no background job whose state can be
lost when the free host restarts: a document either exists fully (all chunks,
status=ready) or not at all.
"""

import hashlib
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.chunking import chunk_blocks
from app.ingestion.parsing import parse_document
from app.llm.embeddings import Embedder
from app.models import Chunk, Document, Workspace


@dataclass
class IngestResult:
    document: Document
    created: bool  # False = identical bytes already in this workspace (no-op)


def chunk_id(workspace_id: uuid.UUID, content_hash: str, index: int) -> str:
    """Deterministic: re-ingesting the same bytes yields the same ids."""
    return hashlib.sha256(f"{workspace_id}:{content_hash}:{index}".encode()).hexdigest()


async def _existing(session: AsyncSession, workspace_id: uuid.UUID, content_hash: str):
    return await session.scalar(
        select(Document).where(
            Document.workspace_id == workspace_id, Document.content_hash == content_hash
        )
    )


async def ingest_document(
    session: AsyncSession,
    embedder: Embedder,
    workspace: Workspace,
    filename: str,
    content_type: str,
    data: bytes,
) -> IngestResult:
    content_hash = hashlib.sha256(data).hexdigest()

    # 1. Idempotency: same bytes in the same workspace -> return the existing doc.
    if existing := await _existing(session, workspace.id, content_hash):
        return IngestResult(existing, created=False)

    # 2. Parse + chunk + embed before touching the DB (raises on bad input / provider failure).
    chunks = chunk_blocks(parse_document(filename, data))
    vectors = await embedder.embed_documents([c.embedding_text() for c in chunks])

    # 3. One transaction: document row + all chunks, or nothing.
    document = Document(
        workspace_id=workspace.id,
        filename=filename,
        content_type=content_type,
        content_hash=content_hash,
        size_bytes=len(data),
        status="ready",
        chunk_count=len(chunks),
    )
    session.add(document)
    try:
        await session.flush()
    except IntegrityError:
        # A concurrent upload of the same file won the race; treat as duplicate.
        await session.rollback()
        return IngestResult(await _existing(session, workspace.id, content_hash), created=False)

    rows = [
        {
            "id": chunk_id(workspace.id, content_hash, c.index),
            "workspace_id": workspace.id,
            "document_id": document.id,
            "chunk_index": c.index,
            "content": c.content,
            "page": c.page,
            "section": c.section,
            "embedding": vector,
        }
        for c, vector in zip(chunks, vectors, strict=True)
    ]
    await session.execute(insert(Chunk).values(rows).on_conflict_do_nothing(index_elements=["id"]))
    await session.commit()
    return IngestResult(document, created=True)
