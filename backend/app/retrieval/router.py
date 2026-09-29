from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from app.auth.deps import SessionDep
from app.llm.embeddings import Embedder, get_embedder
from app.llm.http import ProviderError
from app.retrieval.service import MIN_SIMILARITY, search
from app.workspaces.deps import ActiveWorkspace

router = APIRouter(prefix="/api/workspaces/{workspace_id}/search", tags=["retrieval"])


class SearchOut(BaseModel):
    workspace_id: str
    query: str
    mode: str
    hit: bool
    top_similarity: float | None
    min_similarity: float
    latency_ms: int
    chunks: list[dict]


@router.get("")
async def search_workspace(
    workspace: ActiveWorkspace,
    session: SessionDep,
    embedder: Annotated[Embedder, Depends(get_embedder)],
    q: Annotated[str, Query(min_length=1, max_length=1000)],
    hybrid: bool = True,
) -> SearchOut:
    """Raw retrieval, for the retrieval-debug view: exactly what chat would see."""
    try:
        result = await search(session, embedder, workspace.id, q, hybrid=hybrid)
    except ProviderError:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Embedding service unavailable; retry shortly."
        ) from None
    return SearchOut(
        workspace_id=str(result.workspace_id),
        query=result.query,
        mode=result.mode,
        hit=result.hit,
        top_similarity=result.top_similarity,
        min_similarity=MIN_SIMILARITY,
        latency_ms=result.latency_ms,
        chunks=[c.as_log() for c in result.chunks],
    )
