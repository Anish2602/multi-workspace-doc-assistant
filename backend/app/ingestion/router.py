import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import delete, select

from app.auth.deps import SessionDep
from app.config import get_settings
from app.ingestion.parsing import UnsupportedDocument
from app.ingestion.service import ingest_document
from app.llm.embeddings import Embedder, get_embedder
from app.llm.http import ProviderError
from app.models import Document
from app.workspaces.deps import ActiveWorkspace

router = APIRouter(prefix="/api/workspaces/{workspace_id}/documents", tags=["documents"])

EmbedderDep = Annotated[Embedder, Depends(get_embedder)]


class DocumentOut(BaseModel):
    id: str
    filename: str
    size_bytes: int
    status: str
    chunk_count: int
    created_at: datetime


class UploadOut(BaseModel):
    document: DocumentOut
    duplicate: bool


def _out(doc: Document) -> DocumentOut:
    return DocumentOut(
        id=str(doc.id),
        filename=doc.filename,
        size_bytes=doc.size_bytes,
        status=doc.status,
        chunk_count=doc.chunk_count,
        created_at=doc.created_at,
    )


@router.post("", status_code=status.HTTP_201_CREATED)
async def upload(
    file: UploadFile,
    workspace: ActiveWorkspace,
    session: SessionDep,
    embedder: EmbedderDep,
    response: Response,
) -> UploadOut:
    limit = get_settings().max_upload_bytes
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE, f"File exceeds {limit // (1024 * 1024)} MB"
        )
    filename = (file.filename or "upload").rsplit("/", 1)[-1][:255]
    try:
        result = await ingest_document(
            session, embedder, workspace, filename, file.content_type or "", data
        )
    except UnsupportedDocument as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    except ProviderError:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "The embedding service is busy. Your file was not saved; please retry shortly.",
        ) from None
    if not result.created:
        response.status_code = status.HTTP_200_OK
    await session.refresh(result.document)
    return UploadOut(document=_out(result.document), duplicate=not result.created)


@router.get("")
async def list_documents(workspace: ActiveWorkspace, session: SessionDep) -> list[DocumentOut]:
    docs = await session.scalars(
        select(Document)
        .where(Document.workspace_id == workspace.id)
        .order_by(Document.created_at.desc())
    )
    return [_out(d) for d in docs]


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: uuid.UUID, workspace: ActiveWorkspace, session: SessionDep
) -> None:
    # Scoped by workspace in the same statement: can't delete another workspace's doc.
    result = await session.execute(
        delete(Document).where(Document.id == document_id, Document.workspace_id == workspace.id)
    )
    if result.rowcount == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    await session.commit()
